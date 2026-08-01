"""镇长选举系统：任期周期 / 投票权重 / 候选人状态 / 推选对手。

设计参考 docs/mayor_loop.md §3-4 / §11。

D1 范围（本次 commit）：
- 建表（已在 db.py 完成）
- ElectionStore：start/end_term, get_current_term, get_phase, day_index_in_term
- weight 公式骨架（仅 affection_norm 子项接通，其余占位 0）
- 首期硬编对手 = bear_baker；select_opponent 完整逻辑留 D3
- 不含自动结算 / 时间触发（D2 实现）
"""
from __future__ import annotations

import json
import logging
import random
import time
from typing import Dict, List, Optional, Tuple

from db import get_conn

log = logging.getLogger("election")

# ---------- 常量 ----------
# 3 天任期：D1 危机日 / D2 辩论日 / D3 投票日（节奏收紧，避免中期空转）
TERM_DAYS = 3
DEBATE_DAY_INDEX = 2     # 1-indexed within a term
VOTE_DAY_INDEX = 3

PLAYER_ID = "player"
DEFAULT_FIRST_OPPONENT = "bear_baker"

# 候选对手池（按设计 §4，煊赫极低优先级，不放入默认池）
DEFAULT_OPPONENT_POOL = (
    "bear_baker",
    "fox_postman",
    "herbalist_cui",
    "pirate_lao",
    "traveler_lan",
)

# affection 取值范围（与 affection.py 保持一致）
AFFECTION_VALUE_MIN = -50
AFFECTION_VALUE_MAX = 100
AFFECTION_RANGE = AFFECTION_VALUE_MAX - AFFECTION_VALUE_MIN

# 投票权重子项满分（v2 平衡：兑现承诺 > 拉票互动 > 刷脸）
W_AFFECTION_MAX = 20.0     # 30 → 20：刷脸不再主导
W_PROMISE_MAX = 30.0       # 25 → 30：兑现承诺成最重要
W_DEBATE_MAX = 15.0        # 10 → 15
W_EVENT_MAX = 25.0         # 20 → 25
W_LOYALTY_MAX = 10.0       # 15 → 10
W_TOTAL_MAX = W_AFFECTION_MAX + W_PROMISE_MAX + W_DEBATE_MAX + W_EVENT_MAX + W_LOYALTY_MAX  # 100

# 对手动作机械系数（强劲追赶 + 抹黑反制）
PROMISE_PER_ACTION = 7.0   # 对手每次 promise 动作给目标 voter 的 promise 加分
SMEAR_PER_ACTION = 6.0     # 对手每次 smear 动作在目标 voter 的 event 里给玩家扣分
SMEAR_BACKFIRE = 4.0       # 抹黑玩家铁票（affection≥阈值）时反噬对手自身 event 分
SMEAR_LOYAL_AFFECTION = 40  # voter 对玩家 affection ≥ 此值视为铁票，smear 反噬

# 执政包袱：现任镇长每连任一届累积的权重惩罚，避免当选后靠老本无限连任
INCUMBENT_PENALTY_PER_TERM = 8.0
INCUMBENT_PENALTY_MAX = 24.0
# voter 对现任者好感低于此值时，不满情绪放大惩罚
INCUMBENT_GRUDGE_AFFECTION = 30

# 玩家/小镇谣言对选举 event 分的影响（话题主角==候选人，且该 voter 已知晓时生效）
RUMOR_EVENT_PER = 12.0        # 单条已知晓的候选人谣言，满热度(100)时对该 voter 的 event 分最大影响
RUMOR_SMEAR_BACKFIRE = 0.5   # 护主：抹黑某候选人的铁票选民 → 反向加分系数（越护越挺）

# 任期难度阶梯（陪玩定位）：对手总权重乘此系数，term1 最弱，逐届变强。
# 直接作用于对手每个 voter 的合计权重 → 第一关开局近乎持平。
TERM_DIFFICULTY: Dict[int, float] = {1: 0.5, 2: 0.7, 3: 0.85}
TERM_DIFFICULTY_DEFAULT = 1.0  # term4+ 满难度

# 亲近圈映射（NPC → 亲近 NPC 集合）：仅用于 election 的 loyalty 子项。
# 谣言信念判定已改用 relations.py 的连续关系值，此处由其初始表按
# friend 档（>=30）自动导出，保证两套数据不打架。
def _derive_loyalty_map() -> Dict[str, set]:
    from relations import _NORM_TIES
    m: Dict[str, set] = {}
    for (a, b), v in _NORM_TIES.items():
        if v >= 30:
            m.setdefault(a, set()).add(b)
            m.setdefault(b, set()).add(a)
    return m


LOYALTY_MAP: Dict[str, set] = _derive_loyalty_map()

# 文本情感关键词（粗）：用于 event 子项扫描 world_events.description
POSITIVE_WORDS = ("帮忙", "帮了", "送了", "救了", "答应", "支持", "感谢", "保护", "解决", "礼物")
# 全部用双字以上词条：单字（推/醉/吵）在自然语句里误伤率极高。
NEGATIVE_WORDS = ("争吵", "辱骂", "推搡", "拒绝", "破坏", "失约", "麻烦", "丑闻", "失踪", "酒醉")


# ---------- 工具函数 ----------

def affection_norm(value: int) -> float:
    """affection [-50, 100] → weight [0, W_AFFECTION_MAX=20] 非线性映射。

    分段（让高 affection 的边际收益递减，抑制"无脑刷脸"）：
      [-50,  0]  → [ 0,  4]   斜率 0.08
      [  0, 30]  → [ 4, 12]   斜率 0.27（早期涨得快，鼓励起势）
      [ 30, 60]  → [12, 16]   斜率 0.13
      [ 60,100]  → [16, 20]   斜率 0.10（饱和段，刷脸边际最低）
    """
    if value <= AFFECTION_VALUE_MIN:
        return 0.0
    v = float(value)
    if v <= 0.0:
        return (v + 50.0) * 0.08
    if v <= 30.0:
        return 4.0 + v * (8.0 / 30.0)
    if v <= 60.0:
        return 12.0 + (v - 30.0) * (4.0 / 30.0)
    if v <= 100.0:
        return 16.0 + (v - 60.0) * (4.0 / 40.0)
    return W_AFFECTION_MAX


# ---------- ElectionStore ----------

class ElectionStore:
    """选举状态存取 + 任期生命周期管理。

    使用方：main.py 在 time_tick 时调 ensure_term_active(game_day)，
    LLM 上下文 / HUD 推送时调 get_current_term_view(game_day)。
    """

    def __init__(self, npc_ids: List[str], affection_store=None, world_store=None,
                 promise_store=None) -> None:
        self.npc_ids: List[str] = list(npc_ids)
        self.affection_store = affection_store
        self.world_store = world_store
        self.promise_store = promise_store

    # ---- 任期生命周期 ----

    def get_active_term(self) -> Optional[Dict]:
        """返回未结算的当前任期 row（end_day IS NULL），无则 None。"""
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM election_term WHERE end_day IS NULL ORDER BY term_id DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def get_term_count(self) -> int:
        with get_conn() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM election_term").fetchone()
        return int(row["c"]) if row else 0

    def get_last_finished_term(self) -> Optional[Dict]:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM election_term WHERE end_day IS NOT NULL ORDER BY term_id DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def ensure_term_active(self, game_day: int) -> Dict:
        """如果没有进行中的任期，建一个新的（首期或换届）。返回当前任期 row。

        游戏首次启动 → 首期，对手硬编 bear_baker。
        前一期结算后再调 → 自动开新任期，对手由 select_opponent 选。
        """
        active = self.get_active_term()
        if active is not None:
            return active

        # 没有 active term，需要新建
        opponent = self.select_opponent(prev_term=self.get_last_finished_term())
        term_id = self._create_term(start_day=game_day, opponent_id=opponent)
        log.info("[election] 开新任期 term=%d start_day=%d 对手=%s", term_id, game_day, opponent)
        return self.get_active_term()  # 重新拉一遍含 term_id

    def _create_term(self, start_day: int, opponent_id: str) -> int:
        now = int(time.time())
        with get_conn() as conn:
            cur = conn.execute(
                """INSERT INTO election_term (start_day, end_day, winner_id, opponent_id, result_json, created_at)
                   VALUES (?, NULL, NULL, ?, NULL, ?)""",
                (start_day, opponent_id, now),
            )
            term_id = cur.lastrowid
            # candidate_state 两条：玩家 + 对手
            for cid in (PLAYER_ID, opponent_id):
                conn.execute(
                    """INSERT INTO candidate_state
                       (term_id, candidate_id, is_incumbent, power_points, power_points_max, platform_json)
                       VALUES (?, ?, 0, 0, 3, NULL)""",
                    (term_id, cid),
                )
        return term_id

    def end_term(self, term_id: int, end_day: int, winner_id: str, result: Dict) -> None:
        with get_conn() as conn:
            conn.execute(
                """UPDATE election_term
                   SET end_day = ?, winner_id = ?, result_json = ?
                   WHERE term_id = ?""",
                (end_day, winner_id, json.dumps(result, ensure_ascii=False), term_id),
            )
        log.info("[election] 任期 %d 结束 winner=%s end_day=%d", term_id, winner_id, end_day)

    # ---- 现任 / 权力点（D9）----

    def set_incumbent(self, term_id: int, candidate_id: str) -> None:
        """把某候选人标记为该任期现任（其余候选人清零）。"""
        with get_conn() as conn:
            conn.execute(
                "UPDATE candidate_state SET is_incumbent = 0 WHERE term_id = ?",
                (term_id,),
            )
            conn.execute(
                "UPDATE candidate_state SET is_incumbent = 1 WHERE term_id = ? AND candidate_id = ?",
                (term_id, candidate_id),
            )
        log.info("[election] term=%d 现任 = %s", term_id, candidate_id)

    def is_incumbent(self, term_id: int, candidate_id: str) -> bool:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT is_incumbent FROM candidate_state WHERE term_id = ? AND candidate_id = ?",
                (term_id, candidate_id),
            ).fetchone()
        return bool(row and int(row["is_incumbent"]) == 1)

    def get_incumbent_id(self, term_id: int) -> str:
        """返回该任期现任镇长的 id（player 或 NPC id）；首届无镇长返回 ""。"""
        with get_conn() as conn:
            row = conn.execute(
                "SELECT candidate_id FROM candidate_state WHERE term_id = ? AND is_incumbent = 1 LIMIT 1",
                (term_id,),
            ).fetchone()
        return str(row["candidate_id"]) if row else ""

    def get_candidate_state(self, term_id: int, candidate_id: str) -> Optional[Dict]:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM candidate_state WHERE term_id = ? AND candidate_id = ?",
                (term_id, candidate_id),
            ).fetchone()
        return dict(row) if row else None

    def refresh_power_points(self, term_id: int, candidate_id: str, game_day: int) -> int:
        """跨游戏日则把权力点补满到 power_points_max（不累积）。返回当前点数。

        仅对现任有效；非现任恒 0。
        """
        st = self.get_candidate_state(term_id, candidate_id)
        if st is None:
            return 0
        if int(st.get("is_incumbent", 0)) != 1:
            return 0
        last_day = int(st.get("last_power_day", -1))
        pmax = int(st.get("power_points_max", 3))
        if last_day != game_day:
            with get_conn() as conn:
                conn.execute(
                    """UPDATE candidate_state
                       SET power_points = ?, last_power_day = ?
                       WHERE term_id = ? AND candidate_id = ?""",
                    (pmax, game_day, term_id, candidate_id),
                )
            return pmax
        return int(st.get("power_points", 0))

    def spend_power_points(self, term_id: int, candidate_id: str, cost: int) -> bool:
        """尝试扣 cost 点权力点。足够则扣并返回 True，否则 False。"""
        st = self.get_candidate_state(term_id, candidate_id)
        if st is None or int(st.get("is_incumbent", 0)) != 1:
            return False
        cur = int(st.get("power_points", 0))
        if cur < cost:
            return False
        with get_conn() as conn:
            conn.execute(
                "UPDATE candidate_state SET power_points = ? WHERE term_id = ? AND candidate_id = ?",
                (cur - cost, term_id, candidate_id),
            )
        return True

    def select_opponent(self, prev_term: Optional[Dict]) -> str:
        """选下一期对手（设计 §4 完整版）。

        规则：
        - 首期 → DEFAULT_FIRST_OPPONENT
        - 排除：上届对手（连任）/ 玩家 affection ≥ 50（挚友）/ 间隔 < 2 期重出
        - 未当过对手的 NPC 优先
        - 否则按"距上次担任最久"加权随机
        """
        if prev_term is None:
            return DEFAULT_FIRST_OPPONENT

        # 复仇战：上届玩家落选（赢家是 NPC）→ 下届继续挑战这位现任镇长，
        # 直到玩家夺回。这样现任镇长恒为本届候选人之一，set_incumbent 才命中。
        prev_winner = prev_term.get("winner_id")
        if prev_winner and prev_winner != PLAYER_ID and prev_winner in self.npc_ids:
            return prev_winner

        # 历史对手列表（最新在前）
        with get_conn() as conn:
            rows = conn.execute(
                """SELECT term_id, opponent_id FROM election_term
                   WHERE end_day IS NOT NULL
                   ORDER BY term_id DESC"""
            ).fetchall()
        history = [(int(r["term_id"]), r["opponent_id"]) for r in rows]
        # 每 NPC 上次担任 term_id（不存在则 None）
        last_serve: Dict[str, int] = {}
        for tid, oid in history:
            if oid not in last_serve:
                last_serve[oid] = tid
        latest_tid = history[0][0] if history else 0

        # 候选池：DEFAULT_OPPONENT_POOL ∩ 在场 NPC
        pool = [n for n in DEFAULT_OPPONENT_POOL if n in self.npc_ids]

        def is_close_friend(npc_id: str) -> bool:
            if self.affection_store is None:
                return False
            try:
                return self.affection_store.get(npc_id) >= 50
            except Exception:
                return False

        def gap(npc_id: str) -> int:
            """距上次担任的任期间隔。从未担任过 = +inf。"""
            if npc_id not in last_serve:
                return 9999
            return latest_tid - last_serve[npc_id] + 1

        last_op = prev_term.get("opponent_id") if prev_term else None
        eligible = [
            n for n in pool
            if n != last_op             # 不连任
            and gap(n) >= 2              # 间隔 ≥ 2 期
            and not is_close_friend(n)   # 玩家挚友不被设为对手
        ]
        if not eligible:
            # 兜底：放宽间隔限制
            eligible = [n for n in pool if n != last_op and not is_close_friend(n)]
        if not eligible:
            # 再兜底：随便挑一个不等于上届的
            eligible = [n for n in pool if n != last_op]
        if not eligible:
            return DEFAULT_FIRST_OPPONENT

        # 未担任过的优先
        never_served = [n for n in eligible if n not in last_serve]
        if never_served:
            return random.choice(never_served)

        # 否则按 gap 加权
        weights = [gap(n) for n in eligible]
        return random.choices(eligible, weights=weights, k=1)[0]

    # ---- 节奏判定 ----

    @staticmethod
    def day_index_in_term(term: Dict, game_day: int) -> int:
        """1..N（N 通常 = 7，可超过表示已逾期）。"""
        return max(1, game_day - int(term["start_day"]) + 1)

    def mayor_kind(self, term: Optional[Dict]) -> str:
        """本期镇上有没有镇长、是谁当的。决定三天走哪一套日程。

        返回 'none'（还没选出过镇长）/ 'player'（玩家在位）/ 'npc'（NPC 在位）。
        """
        if not term:
            return "none"
        try:
            mid = self.get_incumbent_id(int(term["term_id"]))
        except Exception:
            return "none"
        if not mid:
            return "none"
        return "player" if mid == PLAYER_ID else "npc"

    def player_is_incumbent(self, term: Optional[Dict]) -> bool:
        """玩家是否为本期现任镇长。"""
        return self.mayor_kind(term) == "player"

    def phase_of(self, day_index: int, term: Optional[Dict] = None) -> str:
        """玩法阶段。只有玩家当镇长时 D2 才是镇务日，其余情况都开辩论。"""
        if day_index >= VOTE_DAY_INDEX:
            return "vote"
        if day_index == DEBATE_DAY_INDEX:
            return "governance" if self.mayor_kind(term) == "player" else "debate"
        return "campaign"

    def day_theme(self, day_index: int, term: Optional[Dict] = None) -> str:
        """当日主题节点（叠加在 phase 之上，用于氛围/引导/玩法开关）。

        三套三天日程，按「镇上现在谁当镇长」分岔：
          无镇长   ：D1 集会日（无事件，安心刷好感）/ D2 辩论日 / D3 投票日
          玩家镇长 ：D1 危机日（调解纠纷）/ D2 镇务日（派人做事）/ D3 投票日
          NPC 镇长 ：D1 八卦日（造谣效果提升）/ D2 辩论日 / D3 投票日
        """
        if day_index >= VOTE_DAY_INDEX:
            return "vote"
        kind = self.mayor_kind(term)
        if day_index == DEBATE_DAY_INDEX:
            return "governance" if kind == "player" else "debate"
        if day_index == 1:
            if kind == "player":
                return "crisis"
            if kind == "npc":
                return "gossip"
            return "rally"
        return "campaign"

    # ---- 权重计算 ----

    def voters_of(self, term: Dict) -> List[str]:
        """该期可投票的 NPC = 全员 NPC - 当期对手（候选人不投自己）。"""
        opponent = term["opponent_id"]
        return [n for n in self.npc_ids if n != opponent]

    def candidates_of(self, term: Dict) -> List[str]:
        return [PLAYER_ID, term["opponent_id"]]

    def compute_weight(
        self,
        voter_id: str,
        candidate_id: str,
        term: Dict,
    ) -> Tuple[float, Dict[str, float]]:
        """计算单个 voter 对单个 candidate 的当前权重（5 子项完整版）。

        affection: voter 对 candidate 的好感度，[-50,100] → [0,50]
        promise: 候选人对该 voter 的承诺兑现率（D6 接通，先返回 0）
        debate: D6 辩论日得分（D7-D8 接通，先返回 0）
        event: 近期 world_events 中与 (voter, candidate) 相关的正负事件累加，[-15, +15]
        loyalty: voter 是否在 candidate 的亲近圈，是则 +5
        """
        sub: Dict[str, float] = {
            "affection": self._calc_affection(voter_id, candidate_id),
            "promise": self._calc_promise(voter_id, candidate_id),
            "debate": self._calc_debate(voter_id, candidate_id, term),
            "event": self._calc_event(voter_id, candidate_id),
            "loyalty": self._calc_loyalty(voter_id, candidate_id),
        }
        # 任期难度阶梯：对手（非玩家）整体权重按届数打折，term1 最弱
        if candidate_id != PLAYER_ID:
            f = self._term_factor(term)
            if f != 1.0:
                sub = {k: v * f for k, v in sub.items()}
        # 执政包袱：现任镇长要为施政结果负责，连任难度递增。
        # 不加这一项的话，玩家一旦当选就能靠累计好感与承诺记录无限连任，
        # 对手 NPC 永远追不上，选举失去悬念。
        incumbency = self._incumbency_penalty(voter_id, candidate_id, term)
        if incumbency != 0.0:
            sub["incumbency"] = incumbency
        total = sum(sub.values())
        return total, sub

    def _incumbency_penalty(self, voter_id: str, candidate_id: str, term: Dict) -> float:
        """现任镇长的执政包袱（负分）。

        - 只对本届的现任者生效，挑战者不受影响
        - 连任越久包袱越重（按已连任届数递增，封顶）
        - voter 对现任者好感越低，越容易把不满算在他头上
        """
        try:
            if not self.is_incumbent(int(term["term_id"]), candidate_id):
                return 0.0
        except Exception:
            return 0.0
        streak = self._incumbent_streak(candidate_id, int(term["term_id"]))
        if streak <= 0:
            return 0.0
        penalty = min(INCUMBENT_PENALTY_PER_TERM * streak, INCUMBENT_PENALTY_MAX)
        # 好感低的 voter 对执政不满更强烈（最多再放大 50%）
        aff = 0.0
        if candidate_id == PLAYER_ID and self.affection_store is not None:
            try:
                aff = float(self.affection_store.get(voter_id))
            except Exception:
                aff = 0.0
        if aff < INCUMBENT_GRUDGE_AFFECTION:
            penalty *= 1.5
        return -penalty

    def _incumbent_streak(self, candidate_id: str, current_term_id: int) -> int:
        """该候选人截至上一届为止的连续在任届数。"""
        streak = 0
        tid = current_term_id
        while tid >= 1:
            try:
                if self.get_incumbent_id(tid) != candidate_id:
                    break
            except Exception:
                break
            streak += 1
            tid -= 1
        return streak

    @staticmethod
    def _term_factor(term: Dict) -> float:
        tid = int(term.get("term_id", 1))
        return TERM_DIFFICULTY.get(tid, TERM_DIFFICULTY_DEFAULT)

    def _calc_promise(self, voter_id: str, candidate_id: str) -> float:
        """promise 子项。

        玩家：基于该 voter 历史 fulfilled/broken 累计（promise_store）。
        对手 NPC：基于本任期内对该 voter 的 'promise' 动作数累计
                  （每次 +PROMISE_PER_ACTION，封顶 W_PROMISE_MAX）。
        clamp 到 [-W_PROMISE_MAX, +W_PROMISE_MAX]。
        """
        if candidate_id == PLAYER_ID:
            if self.promise_store is None:
                return 0.0
            try:
                raw = self.promise_store.calc_score_for_voter(voter_id, PLAYER_ID, terms_back=3)
            except Exception:
                return 0.0
        else:
            # 对手 NPC：每个 promise 动作给该 voter 加分
            try:
                cnt = self._count_opponent_actions(voter_id, candidate_id, "promise")
            except Exception:
                cnt = 0
            raw = cnt * PROMISE_PER_ACTION
        if raw > W_PROMISE_MAX:
            return W_PROMISE_MAX
        if raw < -W_PROMISE_MAX:
            return -W_PROMISE_MAX
        return raw

    def _calc_debate(self, voter_id: str, candidate_id: str, term: Dict) -> float:
        """debate 子项：读 debate_scores（D6 辩论日写入），原始分 [-0.5,1.0] → weight。

        映射：raw * W_DEBATE_MAX，clamp 到 [-W_DEBATE_MAX, W_DEBATE_MAX]。
        辩论未举行（无记录）→ 0。
        """
        term_id = int(term["term_id"])
        with get_conn() as conn:
            row = conn.execute(
                """SELECT score FROM debate_scores
                   WHERE term_id = ? AND voter_id = ? AND candidate_id = ?""",
                (term_id, voter_id, candidate_id),
            ).fetchone()
        if not row:
            return 0.0
        raw = float(row["score"]) * W_DEBATE_MAX
        if raw > W_DEBATE_MAX:
            return W_DEBATE_MAX
        if raw < -W_DEBATE_MAX:
            return -W_DEBATE_MAX
        return raw

    def _calc_affection(self, voter_id: str, candidate_id: str) -> float:
        if candidate_id == PLAYER_ID:
            if self.affection_store is None:
                return 0.0
            return affection_norm(self.affection_store.get(voter_id))
        # NPC 候选人：base 4 起步，每次 visit +3.5，最多累到 W_AFFECTION_MAX
        # 亲近圈再叠加 4 分基础
        base = 4.0
        try:
            visits = self._count_opponent_visits(voter_id, candidate_id)
        except Exception:
            visits = 0
        gain = min(W_AFFECTION_MAX - base - 4.0, visits * 3.5)
        bonus = 4.0 if voter_id in LOYALTY_MAP.get(candidate_id, set()) else 0.0
        return min(W_AFFECTION_MAX, base + gain + bonus)

    def _count_opponent_visits(self, voter_id: str, candidate_id: str) -> int:
        """统计当前 active 任期内，candidate_id（对手）拜访 voter_id 的次数。"""
        return self._count_opponent_actions(voter_id, candidate_id, "visit")

    def _count_opponent_actions(self, voter_id: str, candidate_id: str, action_type: str) -> int:
        """统计当前 active 任期内，candidate_id（对手）对 voter_id 的某类动作次数。"""
        term = self.get_active_term()
        if term is None:
            return 0
        with get_conn() as conn:
            row = conn.execute(
                """SELECT COUNT(*) AS c FROM opponent_actions
                   WHERE term_id = ? AND candidate_id = ? AND target_npc = ?
                     AND action_type = ?""",
                (int(term["term_id"]), candidate_id, voter_id, action_type),
            ).fetchone()
        return int(row["c"]) if row else 0

    def _calc_event(self, voter_id: str, candidate_id: str) -> float:
        """近期 world_events 与 (voter, candidate) 相关的情感累加 + 抹黑机制。

        启发式：扫描最近 N 条 world_events，按正/负关键词命中 ±3。
        抹黑（smear）叠加：
        - 对玩家：对手每次对该 voter 的 smear 动作 → -SMEAR_PER_ACTION
                  （夺走玩家在该 voter 的已得选票）
        - 对对手：若该 voter 是玩家铁票（affection ≥ SMEAR_LOYAL_AFFECTION），
                  smear 反噬 → 对手 -SMEAR_BACKFIRE/次（脏手段惹恼铁票）
        最终 clamp 到 [-W_EVENT_MAX, +W_EVENT_MAX]
        """
        if self.world_store is None:
            return 0.0
        try:
            events = self.world_store.recent(n=30)
        except Exception:
            events = []
        score = 0.0
        for ev in events:
            desc = ev.description or ""
            actor = ev.actor or ""
            # 玩家聊天原文（agent.py 里以「对XX说: …」写入）不参与选情计算：
            # 单字负面词（推/醉/吵）误伤率极高，玩家随口一句「有点麻烦」就会扣分，
            # 且 recent 滑窗会让同一句话反复进出 → 分数无故跳变。
            if actor == PLAYER_ID and desc.startswith("对") and "说:" in desc:
                continue
            mentions_voter = (voter_id in desc) or any(
                tok in desc for tok in voter_id.split("_")
            )
            related = (actor == candidate_id) or (
                mentions_voter and (candidate_id in desc or candidate_id == PLAYER_ID and "玩家" in desc)
            )
            if not related:
                continue
            pos = any(w in desc for w in POSITIVE_WORDS)
            neg = any(w in desc for w in NEGATIVE_WORDS)
            if pos and not neg:
                score += 3.0
            elif neg and not pos:
                score -= 3.0

        # ---- 抹黑机制（基于对手 smear 动作）----
        opponent_id = None
        term = self.get_active_term()
        if term is not None:
            opponent_id = term.get("opponent_id")
        if opponent_id and opponent_id != PLAYER_ID:
            try:
                smear_cnt = self._count_opponent_actions(voter_id, opponent_id, "smear")
            except Exception:
                smear_cnt = 0
            if smear_cnt > 0:
                # voter 对玩家的忠诚度：用平滑系数替代 >=40 硬阈值。
                # 硬阈值会造成阶跃——好感刚跨过 40 的那一刻，对手过去所有 smear
                # 被追溯性反噬，一次性扣 smear_cnt×4，表现为「聊着天对手突然掉分」。
                loyal_k = 0.0
                if self.affection_store is not None:
                    try:
                        aff = float(self.affection_store.get(voter_id))
                    except Exception:
                        aff = 0.0
                    # 好感 25 起开始有反噬，55 达到满额，中间线性过渡
                    lo, hi = float(SMEAR_LOYAL_AFFECTION) - 15.0, float(SMEAR_LOYAL_AFFECTION) + 15.0
                    if aff > lo:
                        loyal_k = min(1.0, (aff - lo) / (hi - lo))
                if candidate_id == PLAYER_ID:
                    # 抹黑削弱玩家在该 voter 的支持
                    score -= smear_cnt * SMEAR_PER_ACTION
                elif candidate_id == opponent_id and loyal_k > 0.0:
                    # 抹黑铁票 → 反噬对手（按忠诚度平滑缩放）
                    score -= smear_cnt * SMEAR_BACKFIRE * loyal_k

        # ---- 玩家/小镇谣言影响（话题主角==候选人，且该 voter「相信」了）----
        # 只有经过信念判定、写入 rumor_belief 且 state='believed' 的才算数。
        # 每人每条只判一次 → 重复造谣不会无限叠加，影响上限 = 全镇人数。
        try:
            with get_conn() as conn:
                rrows = conn.execute(
                    """SELECT r.sentiment AS sentiment
                       FROM rumor_belief b JOIN rumor r ON r.id = b.rumor_id
                       WHERE b.animal_id = ? AND r.subject_id = ?
                         AND b.state = 'believed' AND r.status = 'active'
                         AND r.sentiment IN ('smear', 'praise')""",
                    (voter_id, candidate_id),
                ).fetchall()
        except Exception:
            rrows = []
        if rrows:
            loyal_to_cand = voter_id in LOYALTY_MAP.get(candidate_id, set())
            for rr in rrows:
                mag = RUMOR_EVENT_PER
                if rr["sentiment"] == "praise":
                    score += mag
                elif rr["sentiment"] == "smear":
                    if loyal_to_cand:
                        score += mag * RUMOR_SMEAR_BACKFIRE  # 护主：抹黑铁票反加分
                    else:
                        score -= mag

        # clamp
        if score > W_EVENT_MAX:
            score = W_EVENT_MAX
        elif score < -W_EVENT_MAX:
            score = -W_EVENT_MAX
        return score

    def _calc_loyalty(self, voter_id: str, candidate_id: str) -> float:
        """loyalty 仅给 NPC 候选人（亲近圈机制）。玩家是新人无圈，恒 0。"""
        if candidate_id == PLAYER_ID:
            return 0.0
        # NPC 候选人：voter 在该 NPC 亲近圈 → +满分
        if voter_id in LOYALTY_MAP.get(candidate_id, set()):
            return W_LOYALTY_MAX
        return 0.0

    def recompute_and_persist_weights(self, term: Dict, game_day: int) -> List[Dict]:
        """对当前任期当日所有 (voter, candidate) 重算并写入 election_weight 表。

        返回写入的行（dict 列表）。
        """
        term_id = int(term["term_id"])
        rows: List[Dict] = []
        with get_conn() as conn:
            for voter_id in self.voters_of(term):
                for cand_id in self.candidates_of(term):
                    weight, sub = self.compute_weight(voter_id, cand_id, term)
                    conn.execute(
                        """INSERT INTO election_weight
                           (term_id, game_day, voter_id, candidate_id, weight, breakdown_json)
                           VALUES (?, ?, ?, ?, ?, ?)
                           ON CONFLICT(term_id, game_day, voter_id, candidate_id) DO UPDATE SET
                             weight = excluded.weight,
                             breakdown_json = excluded.breakdown_json""",
                        (term_id, game_day, voter_id, cand_id, weight,
                         json.dumps(sub, ensure_ascii=False)),
                    )
                    rows.append({
                        "voter_id": voter_id,
                        "candidate_id": cand_id,
                        "weight": weight,
                        "breakdown": sub,
                    })
        log.debug("[election] term=%d day=%d 重算权重 %d 行", term_id, game_day, len(rows))
        return rows

    # ---- 视图 / 推送数据 ----

    def latest_weights(self, term: Dict, game_day: Optional[int] = None) -> List[Dict]:
        """读指定一天每个 (voter, candidate) 的 weight 行。

        game_day=None 时读全表 MAX(game_day)；否则读指定日。
        指定日很重要：view 在当日重算后必须读「当日」快照，
        否则会读到残留的更大 game_day 旧快照（含过期子项）。
        """
        term_id = int(term["term_id"])
        with get_conn() as conn:
            if game_day is None:
                rows = conn.execute(
                    """SELECT voter_id, candidate_id, weight, breakdown_json, game_day
                       FROM election_weight
                       WHERE term_id = ?
                         AND game_day = (SELECT MAX(game_day) FROM election_weight WHERE term_id = ?)""",
                    (term_id, term_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT voter_id, candidate_id, weight, breakdown_json, game_day
                       FROM election_weight
                       WHERE term_id = ? AND game_day = ?""",
                    (term_id, game_day),
                ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["breakdown"] = json.loads(d.pop("breakdown_json") or "{}")
            except Exception:
                d["breakdown"] = {}
            out.append(d)
        return out

    def get_current_term_view(self, game_day: int) -> Dict:
        """供 HUD / LLM 上下文使用的统一视图。

        每次调用都即时重算当日权重（5 voters × 2 candidates = 10 行，开销可忽略），
        保证 HUD 反映 affection / event 等变量的当前值。
        """
        term = self.ensure_term_active(game_day)
        # 总是即时重算，保证实时性
        self.recompute_and_persist_weights(term, game_day)
        latest = self.latest_weights(term, game_day)

        day_index = self.day_index_in_term(term, game_day)
        phase = self.phase_of(day_index, term)

        # 玩家权力点（仅当玩家为现任时 > 0；每日 06:00 起跨日补满）
        player_incumbent = self.is_incumbent(int(term["term_id"]), PLAYER_ID)
        player_power = 0
        player_power_max = 0
        if player_incumbent:
            player_power = self.refresh_power_points(int(term["term_id"]), PLAYER_ID, game_day)
            pst = self.get_candidate_state(int(term["term_id"]), PLAYER_ID)
            player_power_max = int(pst.get("power_points_max", 3)) if pst else 3

        # 聚合候选人当前总声望
        score: Dict[str, float] = {cid: 0.0 for cid in self.candidates_of(term)}
        for row in latest:
            cid = row["candidate_id"]
            if cid in score:
                score[cid] += float(row["weight"])

        return {
            "term_id": int(term["term_id"]),
            "start_day": int(term["start_day"]),
            "day_index": day_index,
            "term_days": TERM_DAYS,
            "phase": phase,
            "day_theme": self.day_theme(day_index, term),
            "opponent_id": term["opponent_id"],
            "incumbent_id": self.get_incumbent_id(int(term["term_id"])),
            "candidates": self.candidates_of(term),
            "scores": score,
            "voters": self.voters_of(term),
            "latest_weights": latest,
            "player_incumbent": player_incumbent,
            "player_power": player_power,
            "player_power_max": player_power_max,
        }

    # ---- D7 自动结算 ----

    # 唱票轮数：把选票分几批公布，制造「一轮轮开票」的悬念
    VOTE_ROUNDS = 3

    @staticmethod
    def _split_rounds(ballot_log: List[Dict], candidates: List[str]) -> List[Dict]:
        """把选票切成若干轮，每轮返回该轮明细与累计票数。

        前端据此逐轮唱票；最后一轮的累计票数即最终结果。
        选票顺序先打散，避免每次开票顺序一致、结果一眼看穿。
        """
        order = list(ballot_log)
        random.shuffle(order)
        total = len(order)
        if total == 0:
            return []
        rounds_n = min(ElectionStore.VOTE_ROUNDS, total)
        # 尽量均分，余数摊到靠前的轮次
        base, rest = divmod(total, rounds_n)
        out: List[Dict] = []
        cum: Dict[str, int] = {cid: 0 for cid in candidates}
        idx = 0
        for r in range(rounds_n):
            take = base + (1 if r < rest else 0)
            chunk = order[idx:idx + take]
            idx += take
            for b in chunk:
                cid = b["voted_for"]
                cum[cid] = cum.get(cid, 0) + 1
            out.append({
                "round": r + 1,
                "total_rounds": rounds_n,
                "ballots": [{"voter": b["voter"], "voted_for": b["voted_for"]} for b in chunk],
                "cumulative": dict(cum),
                "is_final": r == rounds_n - 1,
            })
        return out

    def settle_term_if_due(self, game_day: int) -> Optional[Dict]:
        """如果当前 term day_index >= VOTE_DAY_INDEX，触发投票结算并开新任期。

        投票规则（设计 §3）：
        - 每 voter 100% 投票给当前权重最高的 candidate
        - 平局（同一 voter 多候选权重相等） → 随机一票
        - 最终票数最多者胜
        - 候选间票数也平 → 玩家败（保守：维持现状/对手胜）

        返回：结算明细 dict 或 None（未到结算时机）
        """
        term = self.get_active_term()
        if term is None:
            return None
        day_index = self.day_index_in_term(term, game_day)
        if day_index < VOTE_DAY_INDEX:
            return None

        # 结算当日强制重算并读「当日」快照（避免读到残留的旧 game_day 行）
        self.recompute_and_persist_weights(term, game_day)
        latest = self.latest_weights(term, game_day)

        # 按 voter 聚合
        per_voter: Dict[str, Dict[str, float]] = {}
        for row in latest:
            per_voter.setdefault(row["voter_id"], {})[row["candidate_id"]] = float(row["weight"])

        candidates = self.candidates_of(term)
        votes: Dict[str, int] = {cid: 0 for cid in candidates}
        ballot_log: List[Dict] = []
        for voter_id, weights in per_voter.items():
            # 找最大
            max_w = max(weights.values()) if weights else 0.0
            picks = [cid for cid, w in weights.items() if abs(w - max_w) < 1e-6]
            voted = random.choice(picks) if picks else candidates[0]
            votes[voted] = votes.get(voted, 0) + 1
            ballot_log.append({"voter": voter_id, "voted_for": voted, "weights": weights})

        # 决定胜者
        max_votes = max(votes.values()) if votes else 0
        winners = [cid for cid, v in votes.items() if v == max_votes]
        if len(winners) == 1:
            winner_id = winners[0]
        else:
            # 平票 → 玩家败（对手或随机非玩家者胜）
            non_player = [cid for cid in winners if cid != PLAYER_ID]
            winner_id = non_player[0] if non_player else winners[0]

        result = {
            "votes": votes,
            "ballots": ballot_log,
            "rounds": self._split_rounds(ballot_log, candidates),
            "tie_break": len(winners) > 1,
            "winner_id": winner_id,
        }
        # 破诺：所有仍 pending 的承诺标记为 broken（这一步在 end_term 之前，
        # 这样下届的 promise_score 就能反映本届的破诺）
        if self.promise_store is not None:
            try:
                self.promise_store.break_pending_for_term(int(term["term_id"]), game_day)
            except Exception as e:
                log.warning("[election] break promise 失败: %s", e)
        self.end_term(int(term["term_id"]), end_day=game_day, winner_id=winner_id, result=result)

        # 立即开下一任期（D8 起）
        new_term = self.ensure_term_active(game_day + 1)

        # 现任标记：本届胜者成为下届现任（若胜者仍是该期候选人）
        try:
            self.set_incumbent(int(new_term["term_id"]), winner_id)
        except Exception as e:
            log.warning("[election] 设置现任失败: %s", e)

        log.info(
            "[election] 结算 term=%d day=%d 票数=%s winner=%s 下届对手=%s",
            term["term_id"], game_day, votes, winner_id, new_term["opponent_id"]
        )
        return {
            "settled_term_id": int(term["term_id"]),
            "next_term_id": int(new_term["term_id"]),
            "winner_id": winner_id,
            "votes": votes,
            "tie_break": result["tie_break"],
            "next_opponent_id": new_term["opponent_id"],
        }
