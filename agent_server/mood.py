"""心情系统：每只 NPC 的动态情绪（单标量 valence）。

存储：SQLite 表 `mood(animal_id PK, value, updated_at, last_day)`，范围 [MOOD_MIN, MOOD_MAX]。

心情由近期事件推动（送礼 / 聊天 / 好感变化 / 危机 / 听到关于自己的八卦），
并随游戏日惰性衰减回「平静」(0)。用于：
  - 注入 NPC system prompt 的语气；
  - 头顶常驻表情；
  - 影响八卦传播倾向（烦躁爱传坏话）。

单标量简化：负值统一表现为低落 / 烦躁。返回的 dict 会被拼进 reply 包发回 Godot。
"""
from __future__ import annotations

import time
from typing import Dict

from db import get_conn

MOOD_MIN = -100
MOOD_MAX = 100

# 每过一个游戏日，向 0 衰减的幅度（情绪会平复）
DECAY_PER_DAY = 12

# 心情档位（下限阈值，从高到低）→ 标签 + 表情
_LEVELS = [
    ("excited", 40, "兴奋", "😄"),
    ("happy",   15, "开心", "😊"),
    ("calm",   -15, "平静", "😐"),
    ("down",   -40, "低落", "😔"),
    ("upset", MOOD_MIN, "烦躁", "😠"),
]


def level_of(value: int) -> str:
    for name, threshold, _label, _emote in _LEVELS:
        if value >= threshold:
            return name
    return "upset"


def label_of(value: int) -> str:
    for _name, threshold, lb, _emote in _LEVELS:
        if value >= threshold:
            return lb
    return "烦躁"


def emote_of(value: int) -> str:
    for _name, threshold, _lb, em in _LEVELS:
        if value >= threshold:
            return em
    return "😠"


def _snap(value: int) -> Dict[str, object]:
    v = max(MOOD_MIN, min(MOOD_MAX, value))
    return {"value": v, "level": level_of(v), "label": label_of(v), "emote": emote_of(v)}


class MoodStore:
    """每只 NPC 的动态心情。惰性衰减：读取时按经过的游戏日向 0 收敛。"""

    def _row(self, animal_id: str) -> Dict[str, int]:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT value, last_day FROM mood WHERE animal_id = ?",
                (animal_id,),
            ).fetchone()
        if not row:
            return {"value": 0, "last_day": -1}
        return {"value": int(row["value"]),
                "last_day": int(row["last_day"]) if row["last_day"] is not None else -1}

    def _upsert(self, animal_id: str, value: int, last_day: int) -> None:
        v = max(MOOD_MIN, min(MOOD_MAX, value))
        with get_conn() as conn:
            conn.execute(
                """INSERT INTO mood (animal_id, value, updated_at, last_day)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(animal_id) DO UPDATE SET
                     value = excluded.value,
                     updated_at = excluded.updated_at,
                     last_day = excluded.last_day""",
                (animal_id, v, int(time.time()), last_day),
            )

    def _decayed(self, value: int, last_day: int, game_day: int) -> int:
        """按经过的游戏日把 value 向 0 收敛。"""
        if last_day < 0 or game_day <= last_day:
            return value
        days = game_day - last_day
        drift = DECAY_PER_DAY * days
        if value > 0:
            return max(0, value - drift)
        if value < 0:
            return min(0, value + drift)
        return 0

    def get(self, animal_id: str, game_day: int = -1) -> int:
        rec = self._row(animal_id)
        if game_day >= 0:
            return self._decayed(rec["value"], rec["last_day"], game_day)
        return rec["value"]

    def snapshot(self, animal_id: str, game_day: int = -1) -> Dict[str, object]:
        return _snap(self.get(animal_id, game_day))

    def adjust(self, animal_id: str, delta: int, game_day: int = -1) -> Dict[str, object]:
        """先按游戏日衰减，再叠加 delta，落库并返回快照（含 value/level/label/emote/delta）。"""
        rec = self._row(animal_id)
        base = self._decayed(rec["value"], rec["last_day"], game_day) if game_day >= 0 else rec["value"]
        new_v = max(MOOD_MIN, min(MOOD_MAX, base + delta))
        last_day = game_day if game_day >= 0 else rec["last_day"]
        self._upsert(animal_id, new_v, last_day)
        out = _snap(new_v)
        out["delta"] = new_v - base
        return out
