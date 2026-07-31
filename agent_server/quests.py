"""任务系统 - NPC 委托玩家做事，完成给奖励。

3 种任务类型：
  - collect : 玩家收集 N 个 X 物品 → 回到委托人处 chat → 扣道具 + 给奖励
  - relay   : 玩家把一句话传达给 target_npc → 玩家 chat target_npc 时关键词全部命中 → 给奖励
  - deliver : 委托人接受任务时直接把物品交给玩家 → 玩家把物品交给 target_npc chat → 扣道具 + 给奖励

判定原则：
  * 仅依赖"接受任务后"发生的玩家行为（背包、user_text）
  * 接受 + 完成不会同回合发生（接受后立即返回，下次 chat 才检查）
"""
from __future__ import annotations

import json
import random
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple, Callable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
QUESTS_FILE = PROJECT_ROOT / "data" / "world" / "quests.json"

LEVEL_ORDER = {"hate": 0, "cold": 1, "neutral": 2, "warm": 3, "like": 4, "love": 5}

# 同一 NPC 最近完成的这么多个任务，短期内不再重复派发（候选池被清空时才破例）
COOLDOWN_SLOTS = 3



class QuestStore:
    """每个任务的状态记录（单玩家场景，无 player_id 字段）。

    progress 字段：collect 类任务用来记录"已通过送礼累计交付的数量"。
    """

    SCHEMA_VERSION = 3  # v2: 新 3 类型语义；v3: 增加 progress 字段

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._init_table()

    def _conn(self) -> sqlite3.Connection:
        # 按会话路由：连接目标由 db.current_db_path（ContextVar）决定
        import db
        return sqlite3.connect(str(db.current_db_path.get()))

    def _init_table(self) -> None:
        with self._conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS quests_state (
                    quest_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    schema_version INTEGER NOT NULL DEFAULT 1,
                    accepted_at INTEGER NOT NULL,
                    completed_at INTEGER,
                    progress INTEGER NOT NULL DEFAULT 0
                )
            """)
            cur = c.execute("PRAGMA table_info(quests_state)")
            cols = {row[1] for row in cur.fetchall()}
            if "schema_version" not in cols:
                c.execute("ALTER TABLE quests_state ADD COLUMN schema_version INTEGER NOT NULL DEFAULT 1")
            if "progress" not in cols:
                c.execute("ALTER TABLE quests_state ADD COLUMN progress INTEGER NOT NULL DEFAULT 0")
            # 旧 schema 任务全部废弃（语义已变）
            c.execute(
                "UPDATE quests_state SET state='abandoned' WHERE schema_version < ? AND state='active'",
                (self.SCHEMA_VERSION,),
            )

    def get_state(self, quest_id: str) -> Optional[str]:
        with self._conn() as c:
            cur = c.execute(
                "SELECT state FROM quests_state WHERE quest_id = ?",
                (quest_id,),
            )
            row = cur.fetchone()
            return row[0] if row else None

    def get_progress(self, quest_id: str) -> int:
        with self._conn() as c:
            cur = c.execute(
                "SELECT progress FROM quests_state WHERE quest_id = ?",
                (quest_id,),
            )
            row = cur.fetchone()
            return int(row[0]) if row else 0

    def add_progress(self, quest_id: str, delta: int) -> int:
        """累加 progress，返回新值。"""
        with self._conn() as c:
            c.execute(
                "UPDATE quests_state SET progress = progress + ? WHERE quest_id = ?",
                (int(delta), quest_id),
            )
            cur = c.execute("SELECT progress FROM quests_state WHERE quest_id = ?", (quest_id,))
            row = cur.fetchone()
            return int(row[0]) if row else 0

    def get_active_for_giver(self, giver_id: str, defs: Dict) -> Optional[str]:
        with self._conn() as c:
            cur = c.execute("SELECT quest_id FROM quests_state WHERE state = 'active'")
            for (qid,) in cur.fetchall():
                if defs.get(qid, {}).get("npc_id") == giver_id:
                    return qid
        return None

    def get_active_with_target(self, target_id: str, defs: Dict) -> Optional[str]:
        with self._conn() as c:
            cur = c.execute("SELECT quest_id FROM quests_state WHERE state = 'active'")
            for (qid,) in cur.fetchall():
                q = defs.get(qid, {})
                if q.get("kind") in ("relay", "deliver") and q.get("requires", {}).get("target_npc") == target_id:
                    return qid
        return None

    def mark_active(self, quest_id: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO quests_state(quest_id, state, schema_version, accepted_at, completed_at, progress) "
                "VALUES(?, 'active', ?, ?, NULL, 0)",
                (quest_id, self.SCHEMA_VERSION, int(time.time())),
            )

    def mark_completed(self, quest_id: str) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE quests_state SET state='completed', completed_at=? WHERE quest_id=?",
                (int(time.time()), quest_id),
            )

    def recent_completed(self, giver_id: str, defs: Dict, limit: int) -> List[str]:
        """该 NPC 最近完成过的 quest_id（新→旧），用于派发时避开刚做过的。"""
        with self._conn() as c:
            cur = c.execute(
                "SELECT quest_id FROM quests_state WHERE state='completed' "
                "ORDER BY completed_at DESC"
            )
            out: List[str] = []
            for (qid,) in cur.fetchall():
                if defs.get(qid, {}).get("npc_id") != giver_id:
                    continue
                out.append(qid)
                if len(out) >= limit:
                    break
            return out


class QuestEngine:
    """加载 quests.json，提供任务匹配/完成判定。"""

    def __init__(self, store: QuestStore):
        self.store = store
        self._defs: Dict[str, Dict] = {}
        # 可选：判断某 NPC 是否当期竞选对手（对手不派任务，避免承诺加不上选票）
        self.is_opponent = None  # type: Optional[Callable[[str], bool]]
        self._load()

    def _load(self) -> None:
        if not QUESTS_FILE.exists():
            return
        with QUESTS_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        self._defs = {k: v for k, v in data.items() if not k.startswith("_")}

    @property
    def defs(self) -> Dict[str, Dict]:
        return self._defs

    def get(self, quest_id: str) -> Optional[Dict]:
        return self._defs.get(quest_id)

    def eligible_quest_for_offer(self, giver_id: str, affection_level: str) -> Optional[str]:
        """返回该 NPC 可派发的一个任务 ID（玩家好感度足够 + 未完成 + 不在进行中）。

        当期竞选对手不派任务：对手不是投票人，给它完成的承诺无法转化为选票，
        会让玩家"做了任务却不涨竞选分"，体验割裂。
        """
        if self.is_opponent is not None:
            try:
                if self.is_opponent(giver_id):
                    return None
            except Exception:
                pass
        player_lvl = LEVEL_ORDER.get(affection_level, 0)
        candidates: List[str] = []
        for qid, q in self._defs.items():
            if q.get("npc_id") != giver_id:
                continue
            min_lvl = LEVEL_ORDER.get(q.get("min_affection_level", "neutral"), 2)
            if player_lvl < min_lvl:
                continue
            state = self.store.get_state(qid)
            if state == "active":
                continue
            if state == "completed" and not q.get("repeatable", False):
                continue
            candidates.append(qid)
        if not candidates:
            return None
        # 去重冷却：避开该 NPC 最近做过的 N 个模板，除非候选池已被冷却清空。
        # 不这么做的话，可重复任务少的 NPC（如老咸只有一个 collect）会反复派同一件事。
        cooling = set(self.store.recent_completed(giver_id, self._defs, COOLDOWN_SLOTS))
        fresh = [q for q in candidates if q not in cooling]
        if fresh:
            return random.choice(fresh)
        # 全在冷却中 → 取冷却队列里最旧的那个（离上次最久）
        order = self.store.recent_completed(giver_id, self._defs, len(candidates))
        for qid in reversed(order):
            if qid in candidates:
                return qid
        return random.choice(candidates)


    # ---------- 完成判定 ----------

    def try_complete_as_giver(
        self, quest_id: str, inventory: Dict[str, int]
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """委托方角度：collect 类任务在 giver 处检查完成。
        判定：progress + 当前背包数量 ≥ required → 完成
        consume 信息只反映"从背包扣多少"（progress 部分礼物已在送礼时被扣过）。
        返回 (是否完成, consume 信息 {item_id, count} 或 None)
        """
        q = self.get(quest_id)
        if q is None:
            return False, None
        if q.get("kind") != "collect":
            return False, None
        req = q.get("requires", {})
        item_id = req.get("item_id", "")
        needed = int(req.get("count", 1))
        progress = self.store.get_progress(quest_id)
        in_bag = int(inventory.get(item_id, 0))
        if progress + in_bag >= needed:
            # 还差几个从背包扣
            from_bag = max(0, needed - progress)
            consume = {"item_id": item_id, "count": from_bag} if from_bag > 0 else None
            return True, consume
        return False, None

    def try_complete_as_target(
        self, quest_id: str, user_text: str, inventory: Dict[str, int]
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """target 角度：relay/deliver 任务在 target_npc 处检查完成。"""
        q = self.get(quest_id)
        if q is None:
            return False, None
        kind = q.get("kind", "")
        req = q.get("requires", {})
        if kind == "relay":
            keywords: List[str] = req.get("keywords", []) or []
            if not keywords:
                return False, None
            t = (user_text or "").lower()
            hits = sum(1 for kw in keywords if kw.lower() in t)
            # 命中多数关键词即视为「传达了大致意思」（不要求逐字复述）。
            # 阈值 = ceil(总数 × 0.6)，且至少 1 个；3 个关键词 → 命中 2 个即可。
            import math
            threshold = max(1, math.ceil(len(keywords) * 0.6))
            if hits >= threshold:
                return True, None  # relay 不消耗背包道具
            return False, None
        if kind == "deliver":
            item_id = req.get("item_id", "")
            count = int(req.get("count", 1))
            if inventory.get(item_id, 0) >= count:
                return True, {"item_id": item_id, "count": count}
            return False, None
        return False, None
