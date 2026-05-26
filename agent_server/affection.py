"""好感度系统：每只动物对玩家的数值好感 + 等级映射 + delta 规则。

存储：SQLite 独立表 `affection(animal_id PK, value, updated_at, last_greet_day)`，范围 [VALUE_MIN, VALUE_MAX]。

等级映射（6 档，配合 gift 系统的小数值 delta，反馈频繁）：
  hate    : value < -10        → 讨厌（名字板 红色）
  cold    : -10 <= value < 0   → 冷漠（名字板 淡红）
  neutral : 0 <= value < 5     → 中立（名字板 白色，默认）
  warm    : 5 <= value < 15    → 略有好感（名字板 浅黄）
  like    : 15 <= value < 30   → 好感（名字板 淡绿）
  love    : value >= 30        → 喜欢（名字板 绿色）

delta 规则（"有事才跳"，避免廉价感）：
  greet : 每个 NPC 每"游戏日"最多 +1（首次 / 久别重逢）
  chat  : 普通对话 0；含正向词 +2；含负向词 -3（正负互斥时取一边）
  gift  : 公式化（见 gifts.py），最大 +5，普通 +1~+3

返回的 dict 会被 main.py 拼进 reply 包发回 Godot。
"""
from __future__ import annotations

import time
from typing import Dict, Optional

from db import get_conn


VALUE_MIN = -50
VALUE_MAX = 100

# 等级阈值（下限），从高到低排
# 6 档：hate / cold / neutral / warm / like / love
# 配合 gift 系统的小数值 delta，区间收紧到 5-15，
# 送 2-3 次普通礼物就能从 neutral 升到 warm，反馈频繁
_LEVELS = [
    ("love",    30),
    ("like",    15),
    ("warm",     5),
    ("neutral",  0),
    ("cold",   -10),
    ("hate", VALUE_MIN),
]

# 关键词权重（命中即应用，正负互斥取一边）
POSITIVE_WORDS = (
    "喜欢", "谢谢", "感谢", "送你", "送给你", "送给", "厉害",
    "真好", "棒", "可爱", "爱你", "想你", "美味", "好吃",
)
NEGATIVE_WORDS = (
    "讨厌", "滚", "烦", "笨蛋", "丑", "蠢", "垃圾", "死",
    "恶心", "走开",
)


def level_of(value: int) -> str:
    for name, threshold in _LEVELS:
        if value >= threshold:
            return name
    return "hate"


def level_label(level: str) -> str:
    return {
        "hate":    "讨厌",
        "cold":    "冷漠",
        "neutral": "中立",
        "warm":    "略有好感",
        "like":    "好感",
        "love":    "喜欢",
    }.get(level, "中立")


def _classify_text(text: str) -> int:
    """文本好感系数：返回 +2 / -3 / 0。"""
    if not text:
        return 0
    pos_hit = any(w in text for w in POSITIVE_WORDS)
    neg_hit = any(w in text for w in NEGATIVE_WORDS)
    if neg_hit and not pos_hit:
        return -3
    if pos_hit and not neg_hit:
        return +2
    return 0


def delta_for_chat(user_text: str) -> int:
    """玩家发送一句 chat 时应该叠加的 delta（普通对话不加）。"""
    return _classify_text(user_text)


class AffectionStore:
    """每只动物对玩家的好感度。"""

    def get(self, animal_id: str) -> int:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM affection WHERE animal_id = ?",
                (animal_id,),
            ).fetchone()
        return int(row["value"]) if row else 0

    def get_record(self, animal_id: str) -> Dict[str, int]:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT value, last_greet_day FROM affection WHERE animal_id = ?",
                (animal_id,),
            ).fetchone()
        if not row:
            return {"value": 0, "last_greet_day": -1}
        return {
            "value": int(row["value"]),
            "last_greet_day": int(row["last_greet_day"]) if row["last_greet_day"] is not None else -1,
        }

    def _upsert(self, animal_id: str, value: int, last_greet_day: int) -> None:
        v = max(VALUE_MIN, min(VALUE_MAX, value))
        with get_conn() as conn:
            conn.execute(
                """INSERT INTO affection (animal_id, value, updated_at, last_greet_day)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(animal_id) DO UPDATE SET
                     value = excluded.value,
                     updated_at = excluded.updated_at,
                     last_greet_day = excluded.last_greet_day""",
                (animal_id, v, int(time.time()), last_greet_day),
            )

    def adjust(self, animal_id: str, delta: int) -> Dict[str, int | str]:
        """累加 delta（不动 last_greet_day），返回 {value, delta, level}。"""
        rec = self.get_record(animal_id)
        cur = rec["value"]
        new_v = max(VALUE_MIN, min(VALUE_MAX, cur + delta))
        applied = new_v - cur
        if applied != 0:
            self._upsert(animal_id, new_v, rec["last_greet_day"])
        return {
            "value": new_v,
            "delta": applied,
            "level": level_of(new_v),
        }

    def adjust_for_greet(self, animal_id: str, game_day: int) -> Dict[str, int | str]:
        """打招呼：同一游戏日只 +1 一次，返回 {value, delta, level}。"""
        rec = self.get_record(animal_id)
        cur = rec["value"]
        last_day = rec["last_greet_day"]
        if game_day < 0 or game_day == last_day:
            # 同一日已加过 / 客户端没传 day → 不加
            return {"value": cur, "delta": 0, "level": level_of(cur)}
        new_v = max(VALUE_MIN, min(VALUE_MAX, cur + 1))
        applied = new_v - cur
        self._upsert(animal_id, new_v, game_day)
        return {
            "value": new_v,
            "delta": applied,
            "level": level_of(new_v),
        }

    def snapshot(self, animal_id: str) -> Dict[str, int | str]:
        v = self.get(animal_id)
        return {"value": v, "delta": 0, "level": level_of(v)}
