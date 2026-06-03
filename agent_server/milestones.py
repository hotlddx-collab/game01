"""里程碑事件 - 好感度跨越关键等级时触发的特殊对话/礼物。

每个 (npc_id, prev_level→new_level) 组合每个玩家只触发一次，
通过 milestones_unlocked 表记录已触发的组合。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional

import sqlite3

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MILESTONE_FILE = PROJECT_ROOT / "data" / "world" / "milestones.json"


_milestones_cache: Dict[str, Dict[str, Dict]] | None = None


def _load() -> Dict[str, Dict[str, Dict]]:
    global _milestones_cache
    if _milestones_cache is None:
        if MILESTONE_FILE.exists():
            with MILESTONE_FILE.open("r", encoding="utf-8") as f:
                data = json.load(f)
                _milestones_cache = {k: v for k, v in data.items() if not k.startswith("_")}
        else:
            _milestones_cache = {}
    return _milestones_cache


def get_milestone(animal_id: str, prev_level: str, new_level: str) -> Optional[Dict]:
    """返回该等级跃迁的里程碑数据；不存在返回 None。"""
    if prev_level == new_level:
        return None
    data = _load()
    npc_data = data.get(animal_id, {})
    key = f"{prev_level}→{new_level}"
    return npc_data.get(key)


class MilestoneStore:
    """记录哪些里程碑已被触发（每个玩家×NPC×跃迁组合）。"""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._init_table()

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_table(self) -> None:
        with self._conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS milestones_unlocked (
                    animal_id TEXT NOT NULL,
                    transition TEXT NOT NULL,
                    unlocked_at INTEGER NOT NULL,
                    PRIMARY KEY (animal_id, transition)
                )
            """)

    def is_unlocked(self, animal_id: str, transition: str) -> bool:
        with self._conn() as c:
            cur = c.execute(
                "SELECT 1 FROM milestones_unlocked WHERE animal_id = ? AND transition = ?",
                (animal_id, transition),
            )
            return cur.fetchone() is not None

    def mark_unlocked(self, animal_id: str, transition: str) -> None:
        import time
        with self._conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO milestones_unlocked (animal_id, transition, unlocked_at) VALUES (?, ?, ?)",
                (animal_id, transition, int(time.time())),
            )


def maybe_trigger(
    store: MilestoneStore,
    animal_id: str,
    prev_level: str,
    new_level: str,
) -> Optional[Dict]:
    """检查并标记里程碑。
    返回触发的里程碑数据（含 dialog/gift/intent），未触发返回 None。
    """
    if prev_level == new_level:
        return None
    transition = f"{prev_level}→{new_level}"
    if store.is_unlocked(animal_id, transition):
        return None
    data = get_milestone(animal_id, prev_level, new_level)
    if data is None:
        return None
    store.mark_unlocked(animal_id, transition)
    return data
