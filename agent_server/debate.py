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

# 立场反复无常惩罚：只在**同一议题内**反复横跳才罚。
# 跨议题换象限是正常的（真人本来就在钱上务实、在森林上保守），不该罚——
# 旧版按全局统计，反而奖励「全程咬死一格」，正是闭眼选的温床。
CONSISTENCY_FLOOR = 0.55

# 站到某 NPC 的对立象限 → 掉好感。通吃不可能，必须挑阵营。
OPPOSE_AFFECTION_PENALTY = 3      # 每次站对立象限扣的好感基数
OPPOSE_SALIENCE_GATE = 1.0        # 只有该 NPC 在意这个议题时才真的记仇

# 站位可见性：好感达此值即可看穿某 NPC 在某议题上的立场与在意度；
# 否则要事先去打听（intel）。信息差是策略的一半。
STANCE_VISIBLE_AFFECTION = 25


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
        self._offense_applied: Dict[str, int] = {}
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

    @property
    def topic_labels(self) -> Dict[str, str]:
        return self._data.get("_topic_labels", {})

    def stance_pref(self, npc_id: str) -> str:
        """NPC 的本命象限（无议题信息时的兜底）。"""
        return self._data.get("npc_stance_pref", {}).get(npc_id, "pragmatic")

    def stance_on(self, npc_id: str, topic: str) -> str:
        """NPC 在某议题上的实际站位。

        这是消除「闭眼选」的核心：站位不再是一张全局常数表，
        而是随议题变化——老咸在钱上务实、在秩序上激进。
        故每一题的四象限人头分布都不同，不存在恒定最优解。
        """
        by_topic = self._data.get("npc_topic_stance", {}).get(npc_id, {})
        return by_topic.get(topic) or self.stance_pref(npc_id)

    def salience_of(self, npc_id: str, topic: str) -> float:
        """NPC 对某议题的在意度，作为该题的计分权重。

        抓住一个把这题当命根子的人，胜过讨好两个根本不关心的人。
        """
        by_topic = self._data.get("npc_topic_salience", {}).get(npc_id, {})
        try:
            return max(0.0, float(by_topic.get(topic, 1.0)))
        except Exception:
            return 1.0

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
            topic = q.get("topic", "values")
            out.append({
                "asker_id": aid,
                "asker_name": self.personas.get(aid, {}).get("name", aid),
                "q": q.get("q", ""),
                "topic": topic,
                "topic_label": self.topic_labels.get(topic, topic),
                "options": q.get("options", {}),
                "stance_pref": self.stance_on(aid, topic),
                "camps": self.leaning_view(term, topic),
            })
        return out

    # ---- 阵营：NPC 按议题站队 ----

    def npc_leanings(self, term: Dict, topic: str = "") -> Dict[str, str]:
        """本题各 voter 会站到哪个象限。随议题变化，而非全局常数。"""
        if topic:
            return {v: self.stance_on(v, topic)
                    for v in self.election.voters_of(term)}
        return {v: self.stance_pref(v) for v in self.election.voters_of(term)}

    def knows_stance(self, npc_id: str, topic: str) -> bool:
        """玩家是否已掌握该 NPC 在该议题上的立场。

        两条渠道：好感够高（熟到能猜出他怎么想），或事先打听过（intel）。
        都没有则前端显示「？」——辩论前的准备工作因此成为正式玩法。
        """
        try:
            if self.affection_store is not None and \
                    self.affection_store.get(npc_id) >= STANCE_VISIBLE_AFFECTION:
                return True
        except Exception:
            pass
        return self.has_intel(npc_id, topic)

    @staticmethod
    def has_intel(npc_id: str, topic: str) -> bool:
        """是否打听过某 NPC 在某议题上的立场。"""
        try:
            with get_conn() as conn:
                row = conn.execute(
                    """SELECT 1 FROM debate_intel
                       WHERE npc_id = ? AND topic = ? LIMIT 1""",
                    (npc_id, topic),
                ).fetchone()
            return row is not None
        except Exception:
            return False

    @staticmethod
    def record_intel(npc_id: str, topic: str) -> None:
        """记下玩家打听到的某 NPC 议题立场。"""
        try:
            with get_conn() as conn:
                conn.execute(
                    """INSERT OR IGNORE INTO debate_intel (npc_id, topic, created_at)
                       VALUES (?, ?, ?)""",
                    (npc_id, topic, int(time.time())),
                )
        except Exception as e:
            log.warning("[debate] 记录情报失败 %s/%s: %s", npc_id, topic, e)

    def leaning_view(self, term: Dict, topic: str = "") -> List[Dict[str, Any]]:
        """给前端的站队视图：本题每个象限站了哪些 NPC。

        只列出玩家已掌握立场的 NPC；其余归入 unknown，前端显示为「？」。
        """
        leanings = self.npc_leanings(term, topic)
        buckets: Dict[str, List[Dict[str, Any]]] = {s: [] for s in STANCES}
        unknown: List[Dict[str, str]] = []
        for npc_id, stance in leanings.items():
            name = self.personas.get(npc_id, {}).get("name", npc_id)
            if topic and not self.knows_stance(npc_id, topic):
                unknown.append({"npc_id": npc_id, "name": name})
                continue
            if stance in buckets:
                buckets[stance].append({
                    "npc_id": npc_id,
                    "name": name,
                    "salience": round(self.salience_of(npc_id, topic), 2) if topic else 1.0,
                })
        out: List[Dict[str, Any]] = [{
            "stance": s,
            "label": self.stance_labels.get(s, s),
            "npcs": buckets[s],
        } for s in STANCES]
        if unknown:
            out.append({"stance": "unknown", "label": "立场不明", "npcs": unknown})
        return out

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
                        player_history: List[str], topic: str = "") -> str:
        """对手本题选哪个象限。

        他也会看议题：算出本题各象限能拉到多少加权票，扣掉玩家高频立场
        可能分走的部分，取期望最高的一格。带确定性扰动，避免被完全预判
        （旧版 i % 2 交替是可背下来的固定规律，等于白送玩家一个解）。
        """
        opponent_id = term.get("opponent_id", "")
        own = self.stance_pref(opponent_id)
        if not topic:
            return own

        # 本题各象限的加权票仓
        pot: Dict[str, float] = {s: 0.0 for s in STANCES}
        for v in self.election.voters_of(term):
            if v == opponent_id:
                continue
            pot[self.stance_on(v, topic)] += self.salience_of(v, topic)

        # 玩家的立场惯性：他越常站某格，对手去那格越可能只分到一半
        counts: Dict[str, int] = {}
        for s in player_history:
            counts[s] = counts.get(s, 0) + 1
        total = len(player_history)

        rng = random.Random(int(term["term_id"]) * 7919 + q_index * 313)
        best, best_val = own, float("-inf")
        for s in STANCES:
            val = pot[s]
            if total:
                val *= 1.0 - 0.5 * (counts.get(s, 0) / total)
            # 本命象限有性格加成：他不会为了抢票彻底人设崩塌
            if s == self.stance_on(opponent_id, topic):
                val *= 1.25
            val *= 0.85 + 0.3 * rng.random()
            if val > best_val:
                best, best_val = s, val
        return best

    def consistency_factor(self, stances: List[str],
                           topics: Optional[List[str]] = None) -> float:
        """立场连贯度 → [CONSISTENCY_FLOOR, 1.0] 的系数。

        只看**同一议题内**是否反复横跳：同一个议题问到第二次却换了立场，
        才算前后矛盾。跨议题换象限不罚——在钱上务实、在森林上保守，
        本来就是正常人的样子。

        旧版按全局统计，等价于奖励「全程咬死一格」，正好助长闭眼选。
        """
        if not stances:
            return 1.0
        if not topics or len(topics) != len(stances):
            # 无议题信息时退回全局统计（老存档兼容）
            counts: Dict[str, int] = {}
            for s in stances:
                counts[s] = counts.get(s, 0) + 1
            ratio = max(counts.values()) / len(stances)
            t = (ratio - 0.25) / 0.75
            return CONSISTENCY_FLOOR + (1.0 - CONSISTENCY_FLOOR) * max(0.0, min(1.0, t))

        by_topic: Dict[str, List[str]] = {}
        for s, tp in zip(stances, topics):
            by_topic.setdefault(tp, []).append(s)

        flips = 0
        chances = 0
        for group in by_topic.values():
            if len(group) < 2:
                continue
            chances += len(group) - 1
            for a, b in zip(group, group[1:]):
                if a != b:
                    flips += 1
        if not chances:
            return 1.0
        steady = 1.0 - flips / chances
        return CONSISTENCY_FLOOR + (1.0 - CONSISTENCY_FLOOR) * steady

    # ---- 评分 ----

    def score_and_persist(self, term: Dict, answers: Dict[int, str],
                          topics: Optional[Dict[int, str]] = None) -> Dict[str, Any]:
        """answers: {question_index: chosen_stance}；topics: {question_index: topic}。

        逐题按阵营站队结算，且每题按 voter 对该议题的**在意度**加权：
          - voter 与玩家同象限 → 玩家 +CAMP_WIN × salience
          - voter 与对手同象限 → 对手得分，玩家 CAMP_LOSE × salience
          - 两人撞同一象限     → 该象限的 voter 分摊
          - voter 谁也不沾     → 本题双方都不得分
        NPC 每题的站位随议题变化，故不存在「哪格永远人多」的常数解；
        抓住一个把该议题当命根子的人，好过讨好两个不关心的人。

        站到某 voter 的对立象限且他在意该议题 → 掉他好感。通吃不可能。

        一天可开多场：本场答案与此前各场累计后一起重算。
        """
        term_id = int(term["term_id"])
        opponent_id = term["opponent_id"]
        voters = self.election.voters_of(term)
        smeared = self.smeared_voters(term)

        idx_sorted = sorted(answers.keys())
        chosen = [answers[i] for i in idx_sorted if answers[i] in STANCES]
        chosen_topics = [(topics or {}).get(i, "") for i in idx_sorted
                         if answers[i] in STANCES]

        prior_stances, prior_topics = self._prior_answers(term_id)
        all_stances = prior_stances + chosen
        all_topics = prior_topics + chosen_topics
        n_answers = len(all_stances)
        consistency = self.consistency_factor(all_stances, all_topics)

        # 逐题重放对手站位（依赖玩家此前历史，保持可复现）
        opponent_picks: List[str] = []
        for i, _ in enumerate(all_stances):
            opponent_picks.append(
                self.opponent_choice(term, i, all_stances[:i], all_topics[i]))

        player_scores: Dict[str, float] = {}
        opponent_scores: Dict[str, float] = {}
        details: Dict[str, Any] = {}
        offended: Dict[str, int] = {}
        now = int(time.time())
        with get_conn() as conn:
            for voter in voters:
                p_sum = 0.0
                o_sum = 0.0
                weight_sum = 0.0
                oppose_hits = 0.0
                for i, p_stance in enumerate(all_stances):
                    topic = all_topics[i]
                    stance = self.stance_on(voter, topic) if topic \
                        else self.stance_pref(voter)
                    sal = self.salience_of(voter, topic) if topic else 1.0
                    weight_sum += sal
                    o_stance = opponent_picks[i]
                    p_hit = (p_stance == stance)
                    # 已信对手黑料的镇民，本届不会再站到对手那边
                    o_hit = (o_stance == stance) and voter not in smeared
                    if p_hit and o_hit:
                        p_sum += CAMP_WIN * CAMP_SPLIT * sal
                        o_sum += CAMP_WIN * CAMP_SPLIT * sal
                    elif p_hit:
                        p_sum += CAMP_WIN * sal
                    elif o_hit:
                        o_sum += CAMP_WIN * sal
                        p_sum += CAMP_LOSE * sal
                    if _OPPOSITE.get(p_stance) == stance and sal >= OPPOSE_SALIENCE_GATE:
                        oppose_hits += 1
                p_raw = (p_sum / weight_sum * consistency) if weight_sum else 0.0
                o_raw = (o_sum / weight_sum) if weight_sum else 0.0
                player_scores[voter] = p_raw
                opponent_scores[voter] = o_raw
                if oppose_hits:
                    offended[voter] = int(round(OPPOSE_AFFECTION_PENALTY * oppose_hits))
                details[voter] = {
                    "pref": self.stance_pref(voter),
                    "stances_by_q": [self.stance_on(voter, t) if t
                                     else self.stance_pref(voter) for t in all_topics],
                    "salience_by_q": [round(self.salience_of(voter, t), 2) if t else 1.0
                                      for t in all_topics],
                    "player_answers": all_stances,
                    "player_topics": all_topics,
                    "opponent_picks": opponent_picks,
                    "player_raw": p_raw,
                    "opponent_stance": opponent_picks[-1] if opponent_picks else "",
                    "opponent_raw": o_raw,
                    "consistency": consistency,
                    "smeared": voter in smeared,
                    "offended": offended.get(voter, 0),
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

        # 站到对立面的代价：掉好感。只结算本场新增的部分，避免多场重复扣。
        applied = self._apply_offense(offended, len(prior_stances) > 0)

        log.info("[debate] term=%d 评分完成 voters=%d answers=%d consistency=%.2f "
                 "smeared=%d offended=%d",
                 term_id, len(voters), n_answers, consistency, len(smeared), len(applied))
        return {
            "player_scores": player_scores,
            "opponent_scores": opponent_scores,
            "details": details,
            "n_answers": n_answers,
            "consistency": consistency,
            "opponent_picks": opponent_picks,
            "smeared_voters": sorted(smeared),
            "offended": applied,
        }

    def _apply_offense(self, offended: Dict[str, int], has_prior: bool) -> Dict[str, int]:
        """对被站到对立象限的 NPC 扣好感。

        offended 是累计值（含往场），故扣掉此前已扣过的部分再落地。
        """
        if self.affection_store is None or not offended:
            return {}
        applied: Dict[str, int] = {}
        for npc_id, total in offended.items():
            prev = self._offense_applied.get(npc_id, 0) if has_prior else 0
            delta = total - prev
            if delta <= 0:
                continue
            try:
                self.affection_store.adjust(npc_id, -delta)
            except Exception as e:
                log.warning("[debate] 扣好感失败 %s: %s", npc_id, e)
                continue
            self._offense_applied[npc_id] = total
            applied[npc_id] = delta
        return applied

    @staticmethod
    def _prior_answers(term_id: int) -> tuple:
        """取本任期此前各场玩家已给出的立场与对应议题，用于跨场累积。"""
        with get_conn() as conn:
            row = conn.execute(
                """SELECT detail_json FROM debate_scores
                   WHERE term_id = ? AND candidate_id = 'player' LIMIT 1""",
                (term_id,),
            ).fetchone()
        if not row or not row["detail_json"]:
            return [], []
        try:
            data = json.loads(row["detail_json"])
            stances = [s for s in data.get("player_answers", []) if s in STANCES]
            topics = list(data.get("player_topics", []))
            if len(topics) != len(stances):
                topics = [""] * len(stances)
            return stances, topics
        except Exception:
            return [], []

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
