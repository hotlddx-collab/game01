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
DEBATE_DAY_OFFSET = 1  # 辩论日 == 任期第 2 天（day_index=2）

# 辩论日分多场进行。阵营站队玩法单场信息量大（要看全场倾向 + 对手抢票），
# 故压成上午/下午两场、每场 3 题，总题量与旧版持平但节奏更集中。
DEBATE_SESSIONS_PER_DAY = 2
DEBATE_QUESTIONS_PER_SESSION = 3
# 各场开场时刻（游戏小时）
DEBATE_SESSION_HOURS = (9, 15)

# ---- 阵营站队计分 ----
# 逐题结算：玩家选一个象限、对手也选一个象限，NPC 各自站到自己倾向的象限上。
#   与玩家同象限            → 玩家得 CAMP_WIN
#   与对手同象限（玩家不在）→ 对手得 CAMP_WIN，玩家得 CAMP_LOSE
#   两边都不沾             → 谁也不得分
# 玩家与对手撞同一象限时该象限的 NPC 分摊（各得一半），抢票才有意义。
CAMP_WIN = 1.0
CAMP_LOSE = -0.4
CAMP_SPLIT = 0.5

# 立场反复无常惩罚：把玩家所有回答的「立场集中度」折成一个系数乘在总分上。
# 全程咬定 1~2 个象限 → 接近 1.0；每题换一个象限 → 掉到 CONSISTENCY_FLOOR。
# 这是策略张力的来源：想每题都讨好在场的人，就得付出人设崩塌的代价。
CONSISTENCY_FLOOR = 0.55


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
        rumor_store=None,
    ) -> None:
        self.election = election_store
        self.personas = personas
        self.llm = llm
        self.affection_store = affection_store
        self.rumor_store = rumor_store
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

    def pick_questions(self, term: Dict, n: int = 3, session: int = 0) -> List[Dict]:
        """按 term_id + session 作种子，确定性地抽 n 个提问者及其问题。

        提问者优先选「非玩家挚友」（affection<50），保证立场张力；不足再放宽。
        session 为当日第几场（0 起）：不同场次换种子，换提问者也换题，
        避免一天两三场问出同一批问题。
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
        rng = random.Random(term_id * 1009 + 7 + session * 131)
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

    # ---- 阵营：NPC 站队倾向 ----

    def npc_leanings(self, term: Dict) -> Dict[str, str]:
        """本场各 voter 会站到哪个象限。目前即其偏好象限（公开可见）。

        公开是故意的：玩家要能看见「选这个能拿下谁、会得罪谁」，
        取舍才成立——否则又变回盲猜多数派。
        """
        return {v: self.stance_pref(v) for v in self.election.voters_of(term)}

    def leaning_view(self, term: Dict) -> List[Dict[str, Any]]:
        """给前端的站队视图：每个象限站了哪些 NPC。"""
        leanings = self.npc_leanings(term)
        buckets: Dict[str, List[Dict[str, str]]] = {s: [] for s in STANCES}
        for npc_id, stance in leanings.items():
            if stance in buckets:
                buckets[stance].append({
                    "npc_id": npc_id,
                    "name": self.personas.get(npc_id, {}).get("name", npc_id),
                })
        return [{
            "stance": s,
            "label": self.stance_labels.get(s, s),
            "npcs": buckets[s],
        } for s in STANCES]

    def smeared_voters(self, term: Dict) -> set:
        """相信了「对手黑料」的 voter 集合 —— 八卦日造谣在辩论日的回报。

        这些人不会再站到对手那边：哪怕象限对上了，对手也拿不到他们的分。
        """
        out: set = set()
        if self.rumor_store is None:
            return out
        opponent_id = term.get("opponent_id", "")
        if not opponent_id:
            return out
        for voter in self.election.voters_of(term):
            try:
                rows = self.rumor_store.belief_rows_for(voter, opponent_id)
            except Exception:
                continue
            if any(r.get("sentiment") == "smear" for r in rows):
                out.add(voter)
        return out

    def opponent_choice(self, term: Dict, q_index: int,
                        player_history: List[str]) -> str:
        """对手本题选哪个象限。不再是常数——他会主动抢票。

        策略：优先堵玩家的高频立场（玩家老站哪儿，他就去哪儿分票）；
        玩家还没暴露倾向时，退回自己的本命象限。
        """
        own = self.stance_pref(term.get("opponent_id", ""))
        if not player_history:
            return own
        # 玩家最常站的象限
        counts: Dict[str, int] = {}
        for s in player_history:
            counts[s] = counts.get(s, 0) + 1
        hot = max(counts, key=lambda s: counts[s])
        # 隔题交替：一题堵玩家、一题守本命，避免被玩家完全预判
        return hot if q_index % 2 == 0 else own

    @staticmethod
    def consistency_factor(stances: List[str]) -> float:
        """立场集中度 → [CONSISTENCY_FLOOR, 1.0] 的系数。

        用「最高频象限占比」衡量：全程一个象限 = 1.0，
        平均散在 4 个象限 = 0.25 → 映射到下限。
        """
        if not stances:
            return 1.0
        counts: Dict[str, int] = {}
        for s in stances:
            counts[s] = counts.get(s, 0) + 1
        ratio = max(counts.values()) / len(stances)
        # ratio 落在 [0.25, 1.0]，线性映射到 [FLOOR, 1.0]
        t = (ratio - 0.25) / 0.75
        return CONSISTENCY_FLOOR + (1.0 - CONSISTENCY_FLOOR) * max(0.0, min(1.0, t))

    # ---- 评分 ----

    def score_and_persist(self, term: Dict, answers: Dict[int, str]) -> Dict[str, Any]:
        """answers: {question_index: chosen_stance}。逐题按阵营站队结算。

        每一题：玩家站一个象限、对手站一个象限，各 voter 站自己的倾向象限。
          - voter 与玩家同象限 → 玩家 +CAMP_WIN
          - voter 与对手同象限 → 对手 +CAMP_WIN，玩家 CAMP_LOSE（票被抢走）
          - 两人撞同一象限     → 该象限的 voter 分摊（各 CAMP_SPLIT 倍）
          - voter 谁也不沾     → 本题双方都不得分
        最后按玩家立场集中度打一个 consistency 折扣：每题换立场会被全镇看轻。

        八卦日的回报在这里兑现：相信了对手黑料的 voter 不会站到对手那边。

        一天可开多场：本场答案与此前各场累计后一起重算，
        故后一场不覆盖前一场，而是继续修正玩家的整体形象。
        """
        term_id = int(term["term_id"])
        opponent_id = term["opponent_id"]
        voters = self.election.voters_of(term)
        leanings = self.npc_leanings(term)
        smeared = self.smeared_voters(term)

        chosen = [s for s in answers.values() if s in STANCES]
        # 累积历史场次的回答（存在 detail_json 里）
        all_stances = self._prior_answers(term_id) + chosen
        n_answers = len(all_stances)
        consistency = self.consistency_factor(all_stances)

        # 逐题重放，得到对手每题的站位（依赖玩家此前的历史，保持可复现）
        opponent_picks: List[str] = []
        for i, _ in enumerate(all_stances):
            opponent_picks.append(self.opponent_choice(term, i, all_stances[:i]))

        player_scores: Dict[str, float] = {}
        opponent_scores: Dict[str, float] = {}
        details: Dict[str, Any] = {}
        now = int(time.time())
        with get_conn() as conn:
            for voter in voters:
                stance = leanings.get(voter, self.stance_pref(voter))
                p_sum = 0.0
                o_sum = 0.0
                for i, p_stance in enumerate(all_stances):
                    o_stance = opponent_picks[i]
                    p_hit = (p_stance == stance)
                    # 已信对手黑料的镇民，本届不会再站到对手那边
                    o_hit = (o_stance == stance) and voter not in smeared
                    if p_hit and o_hit:
                        p_sum += CAMP_WIN * CAMP_SPLIT
                        o_sum += CAMP_WIN * CAMP_SPLIT
                    elif p_hit:
                        p_sum += CAMP_WIN
                    elif o_hit:
                        o_sum += CAMP_WIN
                        p_sum += CAMP_LOSE
                p_raw = (p_sum / n_answers * consistency) if n_answers else 0.0
                o_raw = (o_sum / n_answers) if n_answers else 0.0
                player_scores[voter] = p_raw
                opponent_scores[voter] = o_raw
                details[voter] = {
                    "pref": stance,
                    "player_answers": all_stances,
                    "opponent_picks": opponent_picks,
                    "player_raw": p_raw,
                    "opponent_stance": opponent_picks[-1] if opponent_picks else "",
                    "opponent_raw": o_raw,
                    "consistency": consistency,
                    "smeared": voter in smeared,
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
        log.info("[debate] term=%d 评分完成 voters=%d answers=%d consistency=%.2f smeared=%d",
                 term_id, len(voters), n_answers, consistency, len(smeared))
        return {
            "player_scores": player_scores,
            "opponent_scores": opponent_scores,
            "details": details,
            "n_answers": n_answers,
            "consistency": consistency,
            "opponent_picks": opponent_picks,
            "smeared_voters": sorted(smeared),
        }

    @staticmethod
    def _prior_answers(term_id: int) -> List[str]:
        """取本任期此前各场辩论玩家已给出的立场，用于跨场累积求平均。"""
        with get_conn() as conn:
            row = conn.execute(
                """SELECT detail_json FROM debate_scores
                   WHERE term_id = ? AND candidate_id = 'player' LIMIT 1""",
                (term_id,),
            ).fetchone()
        if not row or not row["detail_json"]:
            return []
        try:
            return [s for s in json.loads(row["detail_json"]).get("player_answers", [])
                    if s in STANCES]
        except Exception:
            return []

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
