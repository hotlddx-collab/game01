"""玩家承诺池：复用 quests 作内容载体，挂钩选举系统。

设计参考 docs/mayor_loop.md §5。

承诺生命周期：
- 玩家接受 quest（mark_active）→ 同步建一条 pending promise
- quest 完成 → promise fulfilled
- 任期结算（D7 22:00）→ 所有仍 pending 的 promise 标 broken
- 任期末仍 active 的 promise 不会延期到下届，只会被破诺

promise_score 接 weight：
- 每 voter NPC 看自己作为 npc_id 的 promise 历史
- fulfilled +8, broken -10, 累计 clamp 到 [-W_PROMISE_MAX, +W_PROMISE_MAX]
"""
from __future__ import annotations

import json
import logging
import time
from typing import Dict, List, Optional

from db import get_conn

log = logging.getLogger("promises")

# promise score 系数
PROMISE_FULFILL_BONUS = 8.0
PROMISE_BREAK_PENALTY = 10.0


class PromiseStore:
    """承诺池存取。每个 (term_id, candidate, npc, quest) 多次 active 不冲突——
    quest 重复接受会再建新 promise（quests 可 repeatable）。"""

    # ---- 创建 / 查询 ----

    def create(
        self,
        term_id: int,
        candidate_id: str,
        npc_id: str,
        quest_id: str,
        accept_day: int,
        deadline_day: Optional[int] = None,
    ) -> int:
        with get_conn() as conn:
            cur = conn.execute(
                """INSERT INTO promises
                   (term_id, candidate_id, npc_id, quest_id, status,
                    accept_day, deadline_day, created_at)
                   VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)""",
                (term_id, candidate_id, npc_id, quest_id,
                 accept_day, deadline_day, int(time.time())),
            )
            return int(cur.lastrowid or 0)

    def get_pending_for_quest(self, quest_id: str) -> Optional[Dict]:
        with get_conn() as conn:
            row = conn.execute(
                """SELECT * FROM promises
                   WHERE quest_id = ? AND status = 'pending'
                   ORDER BY promise_id DESC LIMIT 1""",
                (quest_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_active_for_term(self, term_id: int, candidate_id: str = "player") -> List[Dict]:
        with get_conn() as conn:
            rows = conn.execute(
                """SELECT * FROM promises
                   WHERE term_id = ? AND candidate_id = ? AND status = 'pending'
                   ORDER BY promise_id DESC""",
                (term_id, candidate_id),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_all_for_term(self, term_id: int, candidate_id: str = "player") -> List[Dict]:
        with get_conn() as conn:
            rows = conn.execute(
                """SELECT * FROM promises
                   WHERE term_id = ? AND candidate_id = ?
                   ORDER BY promise_id DESC""",
                (term_id, candidate_id),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_history_for_voter(
        self,
        voter_id: str,
        candidate_id: str = "player",
        terms_back: int = 3,
    ) -> List[Dict]:
        """供 promise_score 子项使用：返回该 voter 收到的最近 N 期 promise（已结算的）。"""
        with get_conn() as conn:
            rows = conn.execute(
                """SELECT p.* FROM promises p
                   WHERE p.npc_id = ? AND p.candidate_id = ?
                     AND p.status IN ('fulfilled', 'broken')
                   ORDER BY p.promise_id DESC
                   LIMIT 30""",
                (voter_id, candidate_id),
            ).fetchall()
        # 仅返回最近 terms_back 期
        if not rows:
            return []
        # 按 term_id desc 截
        terms_seen = []
        out = []
        for r in rows:
            t = int(r["term_id"])
            if t not in terms_seen:
                if len(terms_seen) >= terms_back:
                    break
                terms_seen.append(t)
            out.append(dict(r))
        return out

    # ---- 状态变更 ----

    def fulfill_by_quest(self, quest_id: str, resolved_day: int) -> Optional[int]:
        """quest 完成时调：把 pending promise 设为 fulfilled。返回 promise_id 或 None。"""
        with get_conn() as conn:
            row = conn.execute(
                "SELECT promise_id FROM promises WHERE quest_id = ? AND status = 'pending'",
                (quest_id,),
            ).fetchone()
            if not row:
                return None
            pid = int(row["promise_id"])
            conn.execute(
                """UPDATE promises SET status='fulfilled', resolved_day=?
                   WHERE promise_id=?""",
                (resolved_day, pid),
            )
        log.info("[promise] fulfill quest=%s pid=%d day=%d", quest_id, pid, resolved_day)
        return pid

    def break_pending_for_term(self, term_id: int, resolved_day: int) -> int:
        """任期结算：扫所有 pending → broken。返回破诺数。"""
        with get_conn() as conn:
            cur = conn.execute(
                """UPDATE promises SET status='broken', resolved_day=?
                   WHERE term_id=? AND status='pending'""",
                (resolved_day, term_id),
            )
            n = cur.rowcount or 0
        if n > 0:
            log.info("[promise] term=%d 破诺 %d 条 (day=%d)", term_id, n, resolved_day)
        return n

    # ---- score 计算 ----

    def calc_score_for_voter(
        self,
        voter_id: str,
        candidate_id: str = "player",
        terms_back: int = 3,
    ) -> float:
        """promise 子项分数：基于该 voter 收到的近 N 期已结算 promise。

        返回 clamp 后值，调用方按需缩放到 W_PROMISE_MAX 范围内。
        """
        history = self.list_history_for_voter(voter_id, candidate_id, terms_back)
        score = 0.0
        for p in history:
            if p["status"] == "fulfilled":
                score += PROMISE_FULFILL_BONUS
            elif p["status"] == "broken":
                score -= PROMISE_BREAK_PENALTY
        return score
