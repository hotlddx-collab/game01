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
from typing import Dict, List, Optional, Any, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
QUESTS_FILE = PROJECT_ROOT / "data" / "world" / "quests.json"

LEVEL_ORDER = {"hate": 0, "cold": 1, "neutral": 2, "warm": 3, "like": 4, "love": 5}


class QuestStore:
    """每个任务的状态记录（单玩家场景，无 player_id 字段）。"""

    SCHEMA_VERSION = 2  # v2: 新 3 类型语义（collect/relay/deliver）

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._init_table()

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_table(self) -> None:
        with self._conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS quests_state (
                    quest_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    schema_version INTEGER NOT NULL DEFAULT 1,
                    accepted_at INTEGER NOT NULL,
                    completed_at INTEGER
                )
            """)
            # 升级旧表：补 schema_version 列
            cur = c.execute("PRAGMA table_info(quests_state)")
            cols = {row[1] for row in cur.fetchall()}
            if "schema_version" not in cols:
                c.execute("ALTER TABLE quests_state ADD COLUMN schema_version INTEGER NOT NULL DEFAULT 1")
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

    def get_active_for_giver(self, giver_id: str, defs: Dict) -> Optional[str]:
        """该 NPC 作为委托方有 active 任务吗？返回 quest_id。"""
        with self._conn() as c:
            cur = c.execute("SELECT quest_id FROM quests_state WHERE state = 'active'")
            for (qid,) in cur.fetchall():
                if defs.get(qid, {}).get("npc_id") == giver_id:
                    return qid
        return None

    def get_active_with_target(self, target_id: str, defs: Dict) -> Optional[str]:
        """有没有一个 active 任务，target_npc 是这个 NPC？返回 quest_id。
        用于 relay/deliver 类任务在玩家找到 target 时识别。"""
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
                "INSERT OR REPLACE INTO quests_state(quest_id, state, schema_version, accepted_at, completed_at) "
                "VALUES(?, 'active', ?, ?, NULL)",
                (quest_id, self.SCHEMA_VERSION, int(time.time())),
            )

    def mark_completed(self, quest_id: str) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE quests_state SET state='completed', completed_at=? WHERE quest_id=?",
                (int(time.time()), quest_id),
            )


class QuestEngine:
    """加载 quests.json，提供任务匹配/完成判定。"""

    def __init__(self, store: QuestStore):
        self.store = store
        self._defs: Dict[str, Dict] = {}
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
        """返回该 NPC 可派发的一个任务 ID（玩家好感度足够 + 未完成 + 不在进行中）。"""
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
        return random.choice(candidates)

    # ---------- 完成判定 ----------

    def try_complete_as_giver(
        self, quest_id: str, inventory: Dict[str, int]
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """委托方角度：collect 类任务在 giver 处检查完成。
        返回 (是否完成, consume 信息 {item_id, count} 或 None)"""
        q = self.get(quest_id)
        if q is None:
            return False, None
        if q.get("kind") != "collect":
            return False, None
        req = q.get("requires", {})
        item_id = req.get("item_id", "")
        count = int(req.get("count", 1))
        if inventory.get(item_id, 0) >= count:
            return True, {"item_id": item_id, "count": count}
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
            if all(kw.lower() in t for kw in keywords):
                return True, None  # relay 不消耗背包道具
            return False, None
        if kind == "deliver":
            item_id = req.get("item_id", "")
            count = int(req.get("count", 1))
            if inventory.get(item_id, 0) >= count:
                return True, {"item_id": item_id, "count": count}
            return False, None
        return False, None
