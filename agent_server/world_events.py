"""公共世界事件流。

玩家行为或重要事件广播到这里，所有动物可以"耳闻"。
G 症状：跨动物消息流通的基础。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import List

from db import get_conn


@dataclass
class WorldEvent:
    id: int
    game_time: str
    real_time: int
    location: str
    actor: str
    description: str

    @classmethod
    def from_row(cls, row) -> "WorldEvent":
        return cls(
            id=row["id"],
            game_time=row["game_time"] or "",
            real_time=row["real_time"],
            location=row["location"] or "",
            actor=row["actor"] or "",
            description=row["description"],
        )


class WorldEventStore:

    def add(
        self,
        actor: str,
        description: str,
        *,
        location: str = "",
        game_time: str = "",
    ) -> int:
        with get_conn() as conn:
            cur = conn.execute(
                """INSERT INTO world_events
                   (game_time, real_time, location, actor, description)
                   VALUES (?, ?, ?, ?, ?)""",
                (game_time, int(time.time()), location, actor, description),
            )
            return cur.lastrowid or 0

    def recent(self, n: int = 10, *, exclude_actor: str = "") -> List[WorldEvent]:
        with get_conn() as conn:
            if exclude_actor:
                rows = conn.execute(
                    """SELECT * FROM world_events WHERE actor != ?
                       ORDER BY real_time DESC LIMIT ?""",
                    (exclude_actor, n),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM world_events ORDER BY real_time DESC LIMIT ?",
                    (n,),
                ).fetchall()
        return [WorldEvent.from_row(r) for r in rows]

    def since(self, after_real_ts: int) -> List[WorldEvent]:
        with get_conn() as conn:
            rows = conn.execute(
                """SELECT * FROM world_events WHERE real_time > ?
                   ORDER BY real_time DESC""",
                (after_real_ts,),
            ).fetchall()
        return [WorldEvent.from_row(r) for r in rows]
