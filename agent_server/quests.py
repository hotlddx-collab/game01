"""任务系统 - NPC 委托玩家做事，完成给奖励。

Quest 类型：
  - collect: 收集物品（玩家凑齐 N 个 X 物品 → 交付）
  - deliver: 跑腿对话（去找另一 NPC 说话）
  - visit:   去某个地点

简化设计：玩家跟 NPC 聊天时自动接受任务。再次跟 NPC 聊天时自动检测完成+奖励。
"""
from __future__ import annotations

import json
import random
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional, Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
QUESTS_FILE = PROJECT_ROOT / "data" / "world" / "quests.json"

# 等级排序，用于 min_affection_level 比较
LEVEL_ORDER = {"hate": 0, "cold": 1, "neutral": 2, "warm": 3, "like": 4, "love": 5}


class QuestStore:
    """每个玩家×任务的状态记录（简化版：单玩家无 player_id 字段）。"""

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
                    accepted_at INTEGER NOT NULL,
                    completed_at INTEGER
                )
            """)

    def get_state(self, quest_id: str) -> Optional[str]:
        with self._conn() as c:
            cur = c.execute(
                "SELECT state FROM quests_state WHERE quest_id = ?",
                (quest_id,),
            )
            row = cur.fetchone()
            return row[0] if row else None

    def get_active_for_npc(self, animal_id: str, defs: Dict) -> Optional[str]:
        """该 NPC 当前是否有 active 任务，返回 quest_id 或 None。"""
        with self._conn() as c:
            cur = c.execute("SELECT quest_id FROM quests_state WHERE state = 'active'")
            for (qid,) in cur.fetchall():
                if defs.get(qid, {}).get("npc_id") == animal_id:
                    return qid
        return None

    def mark_active(self, quest_id: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO quests_state(quest_id, state, accepted_at, completed_at) VALUES(?, 'active', ?, NULL)",
                (quest_id, int(time.time())),
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

    def eligible_quest_for(self, animal_id: str, affection_level: str) -> Optional[str]:
        """返回该 NPC 可派发的一个任务 ID（玩家好感度足够 + 未完成 + 不在进行中）。"""
        player_lvl = LEVEL_ORDER.get(affection_level, 0)
        candidates: List[str] = []
        for qid, q in self._defs.items():
            if q.get("npc_id") != animal_id:
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

    def check_completion(
        self,
        quest_id: str,
        inventory: Dict[str, int],
        visited_locations: List[str],
        talked_to_npcs: List[str],
    ) -> bool:
        """检查活跃任务是否满足完成条件。"""
        q = self.get(quest_id)
        if q is None:
            return False
        kind = q.get("kind", "")
        req = q.get("requires", {})
        if kind == "collect":
            item_id = req.get("item_id", "")
            count = int(req.get("count", 1))
            return inventory.get(item_id, 0) >= count
        elif kind == "visit":
            return req.get("location", "") in visited_locations
        elif kind == "deliver":
            return req.get("target_npc", "") in talked_to_npcs
        return False
