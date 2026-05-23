"""玩家档案：每只动物对玩家的认知 (animal_id, key) → value。"""
from __future__ import annotations

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
