"""好感度系统：每只动物对玩家的数值好感 + 等级映射 + delta 规则。

存储：SQLite 独立表 `affection(animal_id PK, value, updated_at, last_greet_day)`，范围 [VALUE_MIN, VALUE_MAX]。

等级映射（6 档，与名字板颜色一一对应）：
  hostile  : value < 0        → 敌对（名字板 红色）
  neutral  : 0  <= value < 15 → 普通（名字板 白色，默认）
  friendly : 15 <= value < 35 → 友善（名字板 黄色）
  fond     : 35 <= value < 60 → 喜欢（名字板 蓝色）
  close    : 60 <= value < 85 → 好友（名字板 绿色）
  intimate : value >= 85      → 亲密（名字板 深绿）

delta 规则（"有事才跳"，避免廉价感）：
  greet : 每个 NPC 每"游戏日"最多加一次，且增益按档位递减
          （neutral/friendly +1；fond 及以上每 4 天才 +1，堵住零成本刷满）
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
#
# 6 档，且与名字板颜色严格一一对应——档位数不能超过颜色数，
# 否则玩家从名字板读不出自己处在哪一档，多出来的档等于没加：
#   hostile  红    < 0
#   neutral  白    0  ~ 14
#   friendly 黄    15 ~ 34
#   fond     蓝    35 ~ 59
#   close    绿    60 ~ 84
#   intimate 深绿  >= 85
#
# 阈值刻意拉开：旧版顶档门槛只有 30，上限 100 的 70% 区间对等级毫无影响，
# 玩家刷到顶就再无成长反馈——这才是「好感升级困难」的真实成因
# （不是难升，是没得升）。顶档提到 85，30-100 这段才真正被用起来，
# 也给换届衰减留出了可削的纵深。
_LEVELS = [
    ("intimate", 85),
    ("close",    60),
    ("fond",     35),
    ("friendly", 15),
    ("neutral",   0),
    ("hostile", VALUE_MIN),
]

# 从低到高的档位序，供跨模块比较大小用。
# 各处档位判断一律走 at_least()，不要写 == ，
# 否则新增/调整档位时会静默漏掉高档玩家。
LEVEL_ORDER = {
    "hostile": 0, "neutral": 1, "friendly": 2,
    "fond": 3, "close": 4, "intimate": 5,
}


def at_least(level: str, floor: str) -> bool:
    """level 是否达到 floor 档（含）。所有档位判断都该走这里。"""
    return LEVEL_ORDER.get(level, 0) >= LEVEL_ORDER.get(floor, 0)

# 关键词权重（命中即应用，正负互斥取一边）
POSITIVE_WORDS = (
    "喜欢", "谢谢", "感谢", "送你", "送给你", "送给", "厉害",
    "真好", "棒", "可爱", "爱你", "想你", "美味", "好吃",
)
NEGATIVE_WORDS = (
    "讨厌", "滚", "烦", "笨蛋", "丑", "蠢", "垃圾", "死",
    "恶心", "走开",
)


# 打招呼每日增益（按当前档位递减）。
#
# 为什么要递减：打招呼是零成本的——不花道具、不消耗疲劳、不看内容，
# 每天见一面就 +1。这是绕过送礼疲劳与换届衰减的唯一白嫖通道：
# 原本 30 天就能把全镇刷满，让所有平衡设计失效。
#
# 递减后，寒暄只能把关系带到「友善」附近，
# 再往上必须靠送礼、任务、危机抉择——即「点头之交靠碰面，深交靠做事」。
_GREET_GAIN = {
    "hostile":  1,   # 敌对时肯搭理就是破冰，保留完整增益
    "neutral":  1,
    "friendly": 1,
    "fond":     0,   # 蓝档起，单靠寒暄不再涨
    "close":    0,
    "intimate": 0,
}

# fond 及以上：每 N 天寒暄仍给 +1，避免完全归零显得关系「冻住」。
# 留一条极细的通道，但速度不足以支撑刷满。
_GREET_SLOW_EVERY = 4


def greet_gain(value: int, game_day: int) -> int:
    """按当前好感档位算这次打招呼的增益。"""
    lvl = level_of(value)
    base = _GREET_GAIN.get(lvl, 1)
    if base > 0:
        return base
    return 1 if game_day % _GREET_SLOW_EVERY == 0 else 0


def level_of(value: int) -> str:
    for name, threshold in _LEVELS:
        if value >= threshold:
            return name
    return "hostile"


def level_label(level: str) -> str:
    return {
        "hostile":  "敌对",
        "neutral":  "普通",
        "friendly": "友善",
        "fond":     "喜欢",
        "close":    "好友",
        "intimate": "亲密",
    }.get(level, "普通")


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
        """累加 delta（不动 last_greet_day），返回 {value, delta, level, prev_level}。"""
        rec = self.get_record(animal_id)
        cur = rec["value"]
        prev_level = level_of(cur)
        new_v = max(VALUE_MIN, min(VALUE_MAX, cur + delta))
        applied = new_v - cur
        if applied != 0:
            self._upsert(animal_id, new_v, rec["last_greet_day"])
        return {
            "value": new_v,
            "delta": applied,
            "level": level_of(new_v),
            "prev_level": prev_level,
        }

    def adjust_for_greet(self, animal_id: str, game_day: int) -> Dict[str, int | str]:
        """打招呼：同一游戏日只加一次，增益按档位递减，返回 {value, delta, level}。"""
        rec = self.get_record(animal_id)
        cur = rec["value"]
        last_day = rec["last_greet_day"]
        if game_day < 0 or game_day == last_day:
            # 同一日已加过 / 客户端没传 day → 不加
            return {"value": cur, "delta": 0, "level": level_of(cur)}
        gain = greet_gain(cur, game_day)
        new_v = max(VALUE_MIN, min(VALUE_MAX, cur + gain))
        applied = new_v - cur
        # 即使本次没涨也要记 last_greet_day，否则玩家可以反复触发直到撞上「慢通道日」
        self._upsert(animal_id, new_v, game_day)
        return {
            "value": new_v,
            "delta": applied,
            "level": level_of(new_v),
        }

    def snapshot(self, animal_id: str) -> Dict[str, int | str]:
        v = self.get(animal_id)
        return {"value": v, "delta": 0, "level": level_of(v)}
