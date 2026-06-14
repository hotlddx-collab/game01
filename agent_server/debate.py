"""镇长选举：辩论日（D6）系统。

设计参考 docs/mayor_loop.md §6。

流程：
- D6 辩论日，系统从可投票 NPC 中抽 3 个"提问者"（排除玩家挚友 affection≥50，
  保证发问者有立场张力）。
- 每个提问者抛 1 个问题（题库 data/world/debate_questions.json）。
- 玩家从 4 个立场象限选项中选 1（激进改革/稳健保守/讨好民生/务实理性）。
- 对手 LLM 针对玩家的每个回答即时反驳（带兜底文案）。
- 全体 voter NPC 按自己偏好象限给玩家打分（立场吻合度）→ debate_scores。
- debate 子项接 election.compute_weight，仅在本任期生效。

数据无状态：题目按 term_id 作随机种子确定，debate_start 与 debate_finish 一致。
"""
from __future__ import annotations

import json
import logging
import random
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from db import get_conn

log = logging.getLogger("debate")

STANCES = ("radical", "conservative", "pleasing", "pragmatic")

# 立场象限亲和度：玩家选择 vs NPC 偏好。
#   相同 +1.0；对立 -0.5；正交 +0.3
_OPPOSITE = {
    "radical": "conservative",
    "conservative": "radical",
    "pleasing": "pragmatic",
    "pragmatic": "pleasing",
}

# 每个 NPC 对玩家辩论的总分上限缩放（接 W_DEBATE_MAX 在 election 里做）
DEBATE_DAY_OFFSET = 6  # D6 == 任期第 6 天

FALLBACK_REBUTTAL = {
    "radical": "改革？说得轻巧，真动起来镇上得乱成什么样！",
    "conservative": "守着老样子？那要镇长做什么，谁都会喊不折腾。",
    "pleasing": "光会讨好，谁都喜欢，可这能解决半点实事吗？",
    "pragmatic": "算盘打得精，可有些事不是算清楚就能办成的。",
}


def affinity(player_stance: str, npc_pref: str) -> float:
    """玩家立场对某 NPC 偏好的亲和度。"""
    if player_stance == npc_pref:
        return 1.0
    if _OPPOSITE.get(player_stance) == npc_pref:
        return -0.5
    return 0.3


class DebateManager:
    """辩论日逻辑：抽题 / 评分 / 对手反驳。"""

    def __init__(
        self,
        election_store,
        personas: Dict[str, Dict[str, Any]],
        llm=None,
        affection_store=None,
        data_path: Optional[str] = None,
    ) -> None:
        self.election = election_store
        self.personas = personas
        self.llm = llm
        self.affection_store = affection_store
        self._data = self._load_data(data_path)

    def _load_data(self, data_path: Optional[str]) -> Dict[str, Any]:
        if data_path is None:
            here = Path(__file__).resolve().parent
            data_path = str(here.parent / "data" / "world" / "debate_questions.json")
        try:
            with open(data_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log.error("[debate] 题库加载失败 %s: %s", data_path, e)
            return {"npc_stance_pref": {}, "questions": {}, "_stance_labels": {}}

    @property
    def stance_labels(self) -> Dict[str, str]:
        return self._data.get("_stance_labels", {})

    def stance_pref(self, npc_id: str) -> str:
        return self._data.get("npc_stance_pref", {}).get(npc_id, "pragmatic")

    # ---- 抽题 ----

    def pick_questions(self, term: Dict, n: int = 3) -> List[Dict]:
        """按 term_id 作种子，确定性地抽 n 个提问者及其问题。

        提问者优先选「非玩家挚友」（affection<50），保证立场张力；不足再放宽。
        返回 [{asker_id, asker_name, q, options:{stance:text}, stance_pref}]
        """
        term_id = int(term["term_id"])
        voters = self.election.voters_of(term)
        questions_pool = self._data.get("questions", {})
        # 只在有题库的 voter 中选
        candidates = [v for v in voters if v in questions_pool and questions_pool[v]]

        def is_close(npc_id: str) -> bool:
            if self.affection_store is None:
                return False
            try:
                return self.affection_store.get(npc_id) >= 50
            except Exception:
                return False

        preferred = [c for c in candidates if not is_close(c)]
        rng = random.Random(term_id * 1009 + 7)
        askers: List[str]
        if len(preferred) >= n:
            askers = rng.sample(preferred, n)
        else:
            rest = [c for c in candidates if c not in preferred]
            rng.shuffle(rest)
            askers = preferred + rest[: max(0, n - len(preferred))]

        out: List[Dict] = []
        for aid in askers:
            qlist = questions_pool.get(aid, [])
            if not qlist:
                continue
            q = qlist[rng.randrange(len(qlist))]
            out.append({
                "asker_id": aid,
                "asker_name": self.personas.get(aid, {}).get("name", aid),
                "q": q.get("q", ""),
                "options": q.get("options", {}),
                "stance_pref": self.stance_pref(aid),
            })
        return out

    # ---- 评分 ----

    def score_and_persist(self, term: Dict, answers: Dict[int, str]) -> Dict[str, Any]:
        """answers: {question_index: chosen_stance}。

        - 玩家：按每个 voter 偏好象限对玩家所有回答求平均亲和度。
        - 对手：用对手固定立场（其偏好象限）对每个 voter 求亲和度（恒定基线，制造张力）。
        写入 debate_scores 表（含两个候选人）。返回明细。
        """
        term_id = int(term["term_id"])
        opponent_id = term["opponent_id"]
        opponent_stance = self.stance_pref(opponent_id)
        voters = self.election.voters_of(term)
        chosen_stances = [s for s in answers.values() if s in STANCES]
        n_answers = len(chosen_stances)

        player_scores: Dict[str, float] = {}
        opponent_scores: Dict[str, float] = {}
        details: Dict[str, Any] = {}
        now = int(time.time())
        with get_conn() as conn:
            for voter in voters:
                pref = self.stance_pref(voter)
                if n_answers == 0:
                    p_raw = 0.0
                else:
                    p_raw = sum(affinity(s, pref) for s in chosen_stances) / n_answers
                o_raw = affinity(opponent_stance, pref)
                player_scores[voter] = p_raw
                opponent_scores[voter] = o_raw
                details[voter] = {
                    "pref": pref,
                    "player_answers": chosen_stances,
                    "player_raw": p_raw,
                    "opponent_stance": opponent_stance,
                    "opponent_raw": o_raw,
                }
                for cid, raw in (("player", p_raw), (opponent_id, o_raw)):
                    conn.execute(
                        """INSERT INTO debate_scores
                           (term_id, voter_id, candidate_id, score, detail_json, created_at)
                           VALUES (?, ?, ?, ?, ?, ?)
                           ON CONFLICT(term_id, voter_id, candidate_id) DO UPDATE SET
                             score = excluded.score,
                             detail_json = excluded.detail_json,
                             created_at = excluded.created_at""",
                        (term_id, voter, cid, raw,
                         json.dumps(details[voter], ensure_ascii=False), now),
                    )
        log.info("[debate] term=%d 评分完成 voters=%d answers=%d", term_id, len(voters), n_answers)
        return {
            "player_scores": player_scores,
            "opponent_scores": opponent_scores,
            "details": details,
            "n_answers": n_answers,
        }

    @staticmethod
    def get_score(term_id: int, voter_id: str, candidate_id: str = "player") -> Optional[float]:
        """读取某 voter 对某候选人的辩论原始分（[-0.5, 1.0]）。无记录返回 None。"""
        with get_conn() as conn:
            row = conn.execute(
                """SELECT score FROM debate_scores
                   WHERE term_id = ? AND voter_id = ? AND candidate_id = ?""",
                (term_id, voter_id, candidate_id),
            ).fetchone()
        return float(row["score"]) if row else None

    @staticmethod
    def has_debated(term_id: int) -> bool:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM debate_scores WHERE term_id = ?",
                (term_id,),
            ).fetchone()
        return bool(row and int(row["c"]) > 0)

    # ---- 对手反驳 ----

    async def rebut(self, term: Dict, question: str, player_stance: str, player_text: str) -> str:
        """对手针对玩家某个回答的一句反驳。LLM 生成，失败兜底。"""
        opponent_id = term["opponent_id"]
        persona = self.personas.get(opponent_id, {})
        op_name = persona.get("name", opponent_id)

        if self.llm is None:
            return random.choice([FALLBACK_REBUTTAL.get(player_stance, "哼，说得好听。")])

        sys_prompt = (
            f"你扮演 {op_name}，{persona.get('species','怪物')}，正在竞选镇长。"
            f"性格：{persona.get('personality','')}\n"
            f"说话风格：{persona.get('speech_style','')}\n"
            "现在是辩论会，你的对手（玩家）刚回答了一个问题。"
            "请用一句话（不超过 30 字）尖锐地反驳或质疑对方的回答，"
            "要符合你的性格。直接说话，不要旁白，不要引号。"
        )
        user_prompt = (
            f"辩题：{question}\n"
            f"对手的回答：{player_text}\n"
            "你的反驳："
        )
        try:
            resp = await self.llm.chat(
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=80,
                temperature=0.9,
            )
            line = (resp or "").strip().strip("「」\"'")
            if line:
                return line[:80]
        except Exception as e:
            log.warning("[debate] 生成反驳失败: %s", e)
        return FALLBACK_REBUTTAL.get(player_stance, "哼，说得好听，做得到吗？")
