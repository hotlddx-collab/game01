"""动物记忆流。"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import List, Optional, Dict, Any

from db import get_conn


@dataclass
class Memory:
    id: int
    animal_id: str
    game_time: str
    real_time: int
    type: str
    speaker: str
    content: str
    importance: int
    metadata: Dict[str, Any]

    @classmethod
    def from_row(cls, row) -> "Memory":
        meta_raw = row["metadata"]
        meta: Dict[str, Any] = {}
        if meta_raw:
            try:
                meta = json.loads(meta_raw)
            except json.JSONDecodeError:
                pass
        return cls(
            id=row["id"],
            animal_id=row["animal_id"],
            game_time=row["game_time"] or "",
            real_time=row["real_time"],
            type=row["type"],
            speaker=row["speaker"] or "",
            content=row["content"],
            importance=row["importance"] or 5,
            metadata=meta,
        )


class MemoryStore:
    """每只动物独立的记忆流，统一存 SQLite。"""

    def add(
        self,
        animal_id: str,
        content: str,
        *,
        type: str = "dialog",
        speaker: str = "player",
        importance: int = 5,
        game_time: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        meta_str = json.dumps(metadata, ensure_ascii=False) if metadata else None
        with get_conn() as conn:
            cur = conn.execute(
                """INSERT INTO memories
                   (animal_id, game_time, real_time, type, speaker, content, importance, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (animal_id, game_time, int(time.time()), type, speaker, content, importance, meta_str),
            )
            return cur.lastrowid or 0

    def recent(self, animal_id: str, n: int = 20) -> List[Memory]:
        with get_conn() as conn:
            rows = conn.execute(
                """SELECT * FROM memories WHERE animal_id = ?
                   ORDER BY real_time DESC LIMIT ?""",
                (animal_id, n),
            ).fetchall()
        return [Memory.from_row(r) for r in rows]

    def top_important(self, animal_id: str, n: int = 10) -> List[Memory]:
        with get_conn() as conn:
            rows = conn.execute(
                """SELECT * FROM memories WHERE animal_id = ?
                   ORDER BY importance DESC, real_time DESC LIMIT ?""",
                (animal_id, n),
            ).fetchall()
        return [Memory.from_row(r) for r in rows]

    def reflections(self, animal_id: str, n: int = 5) -> List[Memory]:
        with get_conn() as conn:
            rows = conn.execute(
                """SELECT * FROM memories WHERE animal_id = ? AND type = 'reflection'
                   ORDER BY real_time DESC LIMIT ?""",
                (animal_id, n),
            ).fetchall()
        return [Memory.from_row(r) for r in rows]

    def all_since(self, animal_id: str, after_id: int = 0) -> List[Memory]:
        with get_conn() as conn:
            rows = conn.execute(
                """SELECT * FROM memories WHERE animal_id = ? AND id > ?
                   ORDER BY real_time DESC""",
                (animal_id, after_id),
            ).fetchall()
        return [Memory.from_row(r) for r in rows]

    def count(self, animal_id: str) -> int:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM memories WHERE animal_id = ?",
                (animal_id,),
            ).fetchone()
        return row["c"] if row else 0

    def cleanup_old(
        self,
        animal_id: str,
        *,
        keep_recent: int = 60,
        min_importance: int = 5,
        max_total: int = 200,
    ) -> int:
        """清理旧的低重要度记忆，保持 memories 表轻量。

        保留规则（任一满足则保留）：
          1. importance >= min_importance （重要事件永久保留）
          2. 最近 keep_recent 条（无论重要度，保证短期连贯）

        若清理后仍超过 max_total 条，再按时间截断最老的。
        返回删除条数。
        """
        with get_conn() as conn:
            # 找出最近 keep_recent 条的最小 id（保留这个 id 以上的）
            row = conn.execute(
                """SELECT id FROM memories WHERE animal_id = ?
                   ORDER BY real_time DESC LIMIT 1 OFFSET ?""",
                (animal_id, keep_recent - 1),
            ).fetchone()
            cutoff_id = row["id"] if row else 0

            # 删除：id < cutoff_id 且 importance < min_importance
            cur = conn.execute(
                """DELETE FROM memories
                   WHERE animal_id = ?
                     AND id < ?
                     AND importance < ?
                     AND type != 'reflection'""",
                (animal_id, cutoff_id, min_importance),
            )
            deleted = cur.rowcount

            # 二次截断：超 max_total 则删最老的
            count_row = conn.execute(
                "SELECT COUNT(*) AS c FROM memories WHERE animal_id = ?",
                (animal_id,),
            ).fetchone()
            total = count_row["c"] if count_row else 0
            if total > max_total:
                overflow = total - max_total
                cur2 = conn.execute(
                    """DELETE FROM memories WHERE id IN (
                         SELECT id FROM memories WHERE animal_id = ?
                         ORDER BY real_time ASC LIMIT ?
                       )""",
                    (animal_id, overflow),
                )
                deleted += cur2.rowcount

        return deleted
