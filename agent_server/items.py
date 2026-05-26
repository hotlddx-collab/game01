"""物品定义（前后端共用 ID）。

每件物品有：
  - id        : 标识（前后端 = 资源文件名）
  - name      : 中文名（写进 prompt）
  - desc      : 一句描述（写进 prompt）
  - base_value: 礼物基础价值（决定 delta 量级）

base_value 与好感度 delta 的关系（最终公式见 gifts.py）：
    delta = round(base_value × pref_mult × affection_mult × fatigue_mult)

base_value 设计：
  3-4   小心意（花、羽毛）
  5-7   日常（鱼、面包、草药、蘑菇）
  10-15 稀有（水晶、古书）
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


@dataclass(frozen=True)
class ItemDef:
    id: str
    name: str
    desc: str
    base_value: int


_ITEMS: Dict[str, ItemDef] = {
    "flower":       ItemDef("flower",       "野花",   "森林边随手摘的小野花，廉价但有心意",  1),
    "feather":      ItemDef("feather",      "羽毛",   "鸟儿掉下的彩色羽毛，轻盈漂亮",          1),
    "mushroom":     ItemDef("mushroom",     "蘑菇",   "森林里采的可食用蘑菇",                   1),
    "fish":         ItemDef("fish",         "鲜鱼",   "刚从溪水里抓的活鱼",                     1),
    "bread":        ItemDef("bread",        "面包",   "苔老板烤的香喷喷面包",                   2),
    "herb":         ItemDef("herb",         "草药",   "崔草草配的疗愈草药",                     2),
    "crystal":      ItemDef("crystal",      "水晶",   "稀有的紫水晶，会发光",                   3),
    "ancient_book": ItemDef("ancient_book", "古书",   "一本泛黄的羊皮古书，记载着失落的传说",   3),
}


def get(item_id: str) -> ItemDef | None:
    return _ITEMS.get(item_id)


def all_items() -> List[ItemDef]:
    return list(_ITEMS.values())


def all_ids() -> List[str]:
    return list(_ITEMS.keys())
