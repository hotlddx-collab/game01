"""玩家档案：每只动物对玩家的认知 (animal_id, key) → value。"""
from __future__ import annotations

import json
import time
from typing import Dict, Optional

from db import get_conn


class PlayerProfile:
    """每只动物各自维护的"我对玩家的了解"。"""

    def set(self, animal_id: str, key: str, value: str) -> None:
        with get_conn() as conn:
            conn.execute(
                """INSERT INTO player_profile (animal_id, key, value, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(animal_id, key) DO UPDATE SET
                     value = excluded.value,
                     updated_at = excluded.updated_at""",
                (animal_id, key, value, int(time.time())),
            )

    def get(self, animal_id: str, key: str) -> Optional[str]:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM player_profile WHERE animal_id = ? AND key = ?",
                (animal_id, key),
            ).fetchone()
        return row["value"] if row else None

    def get_all(self, animal_id: str) -> Dict[str, str]:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT key, value FROM player_profile WHERE animal_id = ? ORDER BY updated_at DESC",
                (animal_id,),
            ).fetchall()
        return {r["key"]: r["value"] for r in rows}

    def update_many(self, animal_id: str, facts: Dict[str, str]) -> None:
        if not facts:
            return
        for k, v in facts.items():
            self.set(animal_id, k, v)

    # ── forage 库存：NPC 捡到的道具 {item_id: count}，存 key="forage_bag" 的 JSON ──

    _FORAGE_KEY = "forage_bag"

    def get_forage_bag(self, animal_id: str) -> Dict[str, int]:
        raw = self.get(animal_id, self._FORAGE_KEY)
        if not raw:
            return {}
        try:
            data = json.loads(raw)
            return {k: int(v) for k, v in data.items() if int(v) > 0}
        except (ValueError, TypeError):
            return {}

    def forage_inc(self, animal_id: str, item_id: str, delta: int = 1) -> None:
        bag = self.get_forage_bag(animal_id)
        bag[item_id] = bag.get(item_id, 0) + delta
        if bag[item_id] <= 0:
            bag.pop(item_id, None)
        self.set(animal_id, self._FORAGE_KEY, json.dumps(bag, ensure_ascii=False))
