"""礼物系统：公式化好感度计算 + 疲劳累计 + 自然淡忘。

公式：
    delta = round(base_value × pref_mult × affection_mult × fatigue_mult)
    正向增益封顶 GIFT_DELTA_CAP；负向扣分不封顶（保留惩罚力度）。

倍数（实际取值以下方常量为准，改数值时记得同步这段）：
    pref_mult       — NPC 个人偏好（persona.gift_prefs）
        loves    × 1.3
        likes    × 1.1
        neutral  × 1.0
        dislikes × 0.3   （仍是正的，只是几乎不领情）
        hates    × -1.0  （直接扣分，且东西越贵扣得越狠）

    affection_mult  — 当前关系等级（6 档，与名字板颜色一一对应）
        hostile  × 0.3
        neutral  × 1.0
        friendly × 1.1
        fond     × 1.2
        close    × 0.8   高位边际递减：关系越深越难再被同样的礼物打动
        intimate × 0.5

    fatigue_mult    — 同 (npc, item) 累计送礼疲劳，clamp 到 [-0.5, 1.0]
        fatigue_mult = 1.0 - 0.45 × count
        每过 FATIGUE_DECAY_DAYS 个游戏日没送，count -1（自然淡忘）
        **负向礼物例外**：hates 物的疲劳只削弱惩罚（下钳到 0），不会翻成加分。
        见 effective_fatigue_mult()。

存储：SQLite `gift_log(animal_id, item_id, count, last_gift_day, PK)`。
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

from db import get_conn
import items


# ---------- 系数表 ----------

PREF_MULT = {
    "loves":    1.3,
    "likes":    1.1,
    "neutral":  1.0,
    "dislikes": 0.3,
    "hates":   -1.0,
}

AFFECTION_MULT = {
    "hostile":  0.3,
    "neutral":  1.0,
    "friendly": 1.1,
    "fond":     1.2,
    # 高位边际递减：关系越深，同样的礼物越难再打动人。
    # 若继续放大，35-100 这段新拉开的区间会被送礼一口气刷穿，等于白调阈值。
    "close":    0.8,
    "intimate": 0.5,
}

FATIGUE_DECAY_DAYS = 3     # 每过 N 个游戏日没送 → count -1（2 → 3：恢复更慢）
FATIGUE_STEP       = 0.45  # 每次送多了 mult 减少这么多（0.3 → 0.45：疲劳更狠）
FATIGUE_MIN        = -0.5  # 反感封顶
FATIGUE_MAX        = 1.0   # 上限

# 单次送礼好感增益硬上限：无论物品多贵 / 偏好多高，一次最多加这么多。
# 避免送一个高 base_value 物品（如树脂 15）就跳一大截。
GIFT_DELTA_CAP     = 8


# ---------- 偏好分类 ----------

def classify_pref(prefs: Dict[str, Any], item_id: str) -> str:
    """根据 persona.gift_prefs 返回 'loves'/'likes'/'dislikes'/'hates'/'neutral'。"""
    if not prefs:
        return "neutral"
    for key in ("loves", "likes", "dislikes", "hates"):
        ids = prefs.get(key, []) or []
        if item_id in ids:
            return key
    return "neutral"


def pref_label(pref: str) -> str:
    return {
        "loves":    "你最爱的",
        "likes":    "你喜欢的",
        "neutral":  "你没什么特别感觉的",
        "dislikes": "你不太喜欢的",
        "hates":    "你讨厌的",
    }.get(pref, "")


# ---------- 疲劳存储 ----------

class GiftStore:
    """同 (animal, item) 的累计送礼疲劳统计。"""

    def get(self, animal_id: str, item_id: str) -> Dict[str, int]:
        """返回 {count, last_gift_day}；不存在返回 0/-1。"""
        with get_conn() as conn:
            row = conn.execute(
                "SELECT count, last_gift_day FROM gift_log WHERE animal_id=? AND item_id=?",
                (animal_id, item_id),
            ).fetchone()
        if not row:
            return {"count": 0, "last_gift_day": -1}
        return {"count": int(row["count"]), "last_gift_day": int(row["last_gift_day"])}

    def _upsert(self, animal_id: str, item_id: str, count: int, last_gift_day: int) -> None:
        with get_conn() as conn:
            conn.execute(
                """INSERT INTO gift_log (animal_id, item_id, count, last_gift_day, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(animal_id, item_id) DO UPDATE SET
                     count = excluded.count,
                     last_gift_day = excluded.last_gift_day,
                     updated_at = excluded.updated_at""",
                (animal_id, item_id, max(0, count), last_gift_day, int(time.time())),
            )

    def apply_decay(self, animal_id: str, item_id: str, game_day: int) -> int:
        """根据距离上次送礼的天数，自然衰减 count（每 FATIGUE_DECAY_DAYS 天 -1）。
        返回衰减后的 count（不写库；调用方在 register 时一并更新）。
        """
        rec = self.get(animal_id, item_id)
        cur = rec["count"]
        last = rec["last_gift_day"]
        if cur <= 0 or game_day < 0 or last < 0:
            return cur
        gap_days = max(0, game_day - last)
        decay = gap_days // FATIGUE_DECAY_DAYS
        return max(0, cur - decay)

    def register(self, animal_id: str, item_id: str, game_day: int, decayed_count_before: int) -> int:
        """记录一次送礼。decayed_count_before 是 apply_decay 后、本次送之前的 count。
        本次送完 count = decayed + 1。返回新的 count。
        """
        new_count = max(0, decayed_count_before) + 1
        self._upsert(animal_id, item_id, new_count, max(-1, game_day))
        return new_count


# ---------- 全局 delta 计算 ----------

def compute_fatigue_mult(count_before_this_gift: int) -> float:
    """count 是本次送之前（apply_decay 之后）的累计次数。"""
    raw = 1.0 - FATIGUE_STEP * max(0, count_before_this_gift)
    return max(FATIGUE_MIN, min(FATIGUE_MAX, raw))


def effective_fatigue_mult(fatigue_mult: float, pref_mult: float) -> float:
    """负向礼物不吃「疲劳翻正」。

    疲劳的本意是「同一件东西送多了就不新鲜」，套到扣分物上却成了
    「同一句难听话骂多了反而变成夸奖」——因为 fatigue 会一路降到 -0.5，
    而 负 pref_mult × 负 fatigue = 正。玩家只要连送 4 次对方最讨厌的东西，
    第 4 次就开始加分，是实打实的刷分漏洞。

    对 hates 物：疲劳只能削弱惩罚力度（趋近 0），不能把符号翻过来。
    """
    if pref_mult < 0:
        return max(0.0, fatigue_mult)
    return fatigue_mult


def compute_delta(
    item_id: str,
    persona_prefs: Dict[str, Any],
    affection_level: str,
    count_before_this_gift: int,
) -> Dict[str, Any]:
    """核心公式。返回 {delta, base, pref, pref_mult, affection_mult, fatigue_mult, count_after}。
    delta 已 round 为 int。
    """
    item = items.get(item_id)
    if item is None:
        return {"delta": 0, "base": 0, "pref": "neutral", "pref_mult": 1.0,
                "affection_mult": 1.0, "fatigue_mult": 1.0, "count_after": count_before_this_gift + 1,
                "error": f"未知物品 {item_id}"}

    pref = classify_pref(persona_prefs or {}, item_id)
    pm = PREF_MULT.get(pref, 1.0)
    am = AFFECTION_MULT.get(affection_level, 1.0)
    fm = compute_fatigue_mult(count_before_this_gift)
    fm_eff = effective_fatigue_mult(fm, pm)
    raw = item.base_value * pm * am * fm_eff
    delta = int(round(raw))
    # 硬上限：正向增益封顶（负向扣分不限制，保留惩罚力度）
    if delta > GIFT_DELTA_CAP:
        delta = GIFT_DELTA_CAP

    return {
        "delta": delta,
        "base": item.base_value,
        "pref": pref,
        "pref_mult": pm,
        "affection_mult": am,
        "fatigue_mult": fm_eff,
        "count_after": count_before_this_gift + 1,
        "raw": raw,
    }
