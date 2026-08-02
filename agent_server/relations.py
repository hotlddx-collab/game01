"""NPC 之间的亲疏关系网：连续值取代原先的布尔好友集合。

原设计 LOYALTY_MAP 是 Dict[str, set]，非好友即陌生，造成两个问题：
  1. 造谣只有 0%（听者是目标好友，直接 -45 压死）和 100%（毫无抗性）两种结果；
  2. 关系永远静态，玩家无法通过任何手段改变镇上的人际格局。

改为无序对上的连续值 value ∈ [-100, 100]：
  >= 60  挚友      听到坏话强烈护主，也最愿意信对方的话
  30~59  朋友
  10~29  相熟
  -9~9   点头之交（默认）
  -29~-10 有隔阂
  <= -30 不对付   听到目标的坏话反而更信

关系会演化：互聊拉近、玩家挑拨拉远、谣言被采信拉远。
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple

from db import get_conn

VALUE_MIN = -100
VALUE_MAX = 100

# 等级阈值（下限），从高到低
_LEVELS = [
    ("close",    60),
    ("friend",   30),
    ("familiar", 10),
    ("neutral",  -9),
    ("distant", -29),
    ("hostile", VALUE_MIN),
]

_LABELS = {
    "close":    "情同手足",
    "friend":   "关系不错",
    "familiar": "算得上熟",
    "neutral":  "点头之交",
    "distant":  "有点隔阂",
    "hostile":  "很不对付",
}


def level_of(value: int) -> str:
    for name, threshold in _LEVELS:
        if value >= threshold:
            return name
    return "hostile"


def label_of(value: int) -> str:
    return _LABELS.get(level_of(value), "点头之交")


# ---- 初始关系（依据 data/animals/*.json 的人物设定）----
# 刻意做出强弱落差与两道裂缝，让玩家有下手的地方：
#   · 苔老板不再人人都好——他慢性子，跟话密的小蓝、冷淡的煊赫都不熟。
#   · 焰仔↔小蓝 互相看不惯（一个嫌吵一个爱缠），是天然突破口。
#   · 煊赫除老咸外与全镇疏离，最容易被说动。
_TIES: Dict[Tuple[str, str], int] = {
    ("bear_baker", "fox_postman"):    58,   # 天天拌嘴的老邻居，感情最深
    ("bear_baker", "herbalist_cui"):  52,   # 一个留面包一个送薄荷，安静的默契
    ("bear_baker", "pirate_lao"):     18,
    ("bear_baker", "traveler_lan"):   12,   # 小蓝敬他，他只是听着
    ("bear_baker", "mystic_xuan"):     2,
    ("fox_postman", "herbalist_cui"): 36,   # 毒舌肯喝她的清火茶
    ("fox_postman", "pirate_lao"):    28,   # 送信送出来的交情
    ("fox_postman", "traveler_lan"): -22,   # 嫌小蓝话多缠人，小蓝偏爱找他
    ("fox_postman", "mystic_xuan"):   -6,
    ("herbalist_cui", "traveler_lan"): 24,  # 一个安静一个话密，正好互补
    ("herbalist_cui", "pirate_lao"):   8,
    ("herbalist_cui", "mystic_xuan"): -4,
    ("pirate_lao", "mystic_xuan"):    64,   # 心照不宣的旧相识，全镇最铁
    ("pirate_lao", "traveler_lan"):   30,   # 一个爱讲一个爱听
    ("mystic_xuan", "traveler_lan"): -18,   # 煊赫烦她刨根问底

    # ---- 备选池 3 人（未上场时不参与，轮换迁入后即刻生效）----
    # 设计意图：新人不是"空降的老好人"，一进镇就自带立场与摩擦，
    # 否则轮换只是换张脸，镇上的关系格局毫无变化。
    ("boar_shi", "bear_baker"):      45,   # 硬汉敬重不吹牛的老实人
    ("boar_shi", "traveler_lan"):   -25,   # 嫌她话多，她偏爱缠着问
    ("boar_shi", "mole_tu"):         -8,   # 大嗓门吓得鼹鼠往洞里钻
    ("boar_shi", "diva_mei"):       -12,   # 一个嫌花哨一个嫌土
    ("boar_shi", "fox_postman"):     20,
    ("boar_shi", "herbalist_cui"):   16,
    ("boar_shi", "pirate_lao"):      32,   # 都是干过粗活的，聊得来
    ("boar_shi", "mystic_xuan"):      0,
    ("mole_tu", "mystic_xuan"):      55,   # 夜行者同盟，全镇第二铁
    ("mole_tu", "diva_mei"):        -30,   # 全镇最大裂缝，调解难度最高
    ("mole_tu", "pirate_lao"):       26,   # 老咸爱讲，老土爱听
    ("mole_tu", "herbalist_cui"):    22,   # 都安静，互不打扰
    ("mole_tu", "bear_baker"):       14,
    ("mole_tu", "fox_postman"):      -4,
    ("mole_tu", "traveler_lan"):    -10,   # 怕她把听来的话传出去
    ("diva_mei", "herbalist_cui"):   15,   # 塑料姐妹：表面和气，暗自较劲
    ("diva_mei", "traveler_lan"):    40,   # 一个爱美一个爱聊，天生一对话搭子
    ("diva_mei", "bear_baker"):      28,
    ("diva_mei", "fox_postman"):     12,
    ("diva_mei", "pirate_lao"):       6,
    ("diva_mei", "mystic_xuan"):    -14,   # 嫌他阴沉，他嫌她吵
}


def _key(a: str, b: str) -> Tuple[str, str]:
    """无序对规范化，保证 (a,b) 与 (b,a) 落到同一行。"""
    return (a, b) if a <= b else (b, a)


# _TIES 手写时未按字典序，统一规范化后再查表
_NORM_TIES: Dict[Tuple[str, str], int] = {
    _key(a, b): v for (a, b), v in _TIES.items()
}


def initial_value(a: str, b: str) -> int:
    return _NORM_TIES.get(_key(a, b), 0)


# ---- 演化幅度 ----
D_CHAT = 1          # 一次自发互聊：慢慢熟络
D_RUMOR_BELIEVED = -8   # 听信了关于对方的坏话：关系受损
D_RUMOR_PRAISE = 3      # 听信了关于对方的好话
D_DEBUNK = 4            # 玩家帮忙辟谣，替对方说话


class RelationStore:
    """NPC↔NPC 亲疏关系读写。首次访问某对时按 _TIES 落初值。"""

    def get(self, a: str, b: str) -> int:
        if a == b:
            return VALUE_MAX
        ka, kb = _key(a, b)
        with get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM npc_relation WHERE a_id = ? AND b_id = ?",
                (ka, kb),
            ).fetchone()
        if row is not None:
            return int(row["value"])
        # 未落库 → 写入初值，之后即可演化
        init = initial_value(ka, kb)
        self._write(ka, kb, init)
        return init

    def _write(self, ka: str, kb: str, value: int) -> None:
        v = max(VALUE_MIN, min(VALUE_MAX, value))
        with get_conn() as conn:
            conn.execute(
                """INSERT INTO npc_relation (a_id, b_id, value, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(a_id, b_id) DO UPDATE SET
                     value = excluded.value,
                     updated_at = excluded.updated_at""",
                (ka, kb, v, int(time.time())),
            )

    def adjust(self, a: str, b: str, delta: int) -> int:
        """调整并返回新值。"""
        if a == b or delta == 0:
            return self.get(a, b)
        cur = self.get(a, b)
        new = max(VALUE_MIN, min(VALUE_MAX, cur + delta))
        ka, kb = _key(a, b)
        self._write(ka, kb, new)
        return new

    def neighbors(self, animal_id: str, all_ids: List[str]) -> List[Dict]:
        """该 NPC 与其他所有人的关系，按亲疏降序。"""
        out = []
        for other in all_ids:
            if other == animal_id:
                continue
            v = self.get(animal_id, other)
            out.append({"id": other, "value": v,
                        "level": level_of(v), "label": label_of(v)})
        out.sort(key=lambda d: -d["value"])
        return out

    def closest(self, animal_id: str, all_ids: List[str]) -> Optional[Dict]:
        rows = self.neighbors(animal_id, all_ids)
        return rows[0] if rows else None

    def worst(self, animal_id: str, all_ids: List[str]) -> Optional[Dict]:
        rows = self.neighbors(animal_id, all_ids)
        return rows[-1] if rows else None
