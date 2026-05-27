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
    # ── 食物（base 1-2）──────────────────────
    "flower":       ItemDef("flower",       "野花",     "森林边随手摘的小野花，廉价但有心意",            1),
    "feather":      ItemDef("feather",      "羽毛",     "鸟儿掉下的彩色羽毛，轻盈漂亮",                  1),
    "mushroom":     ItemDef("mushroom",     "蘑菇",     "森林里采的可食用蘑菇",                          1),
    "fish":         ItemDef("fish",         "鲜鱼",     "刚从溪水里抓的活鱼",                            1),
    "acorn":        ItemDef("acorn",        "橡果",     "树下捡的饱满橡果，秋天的味道",                   1),
    "letter":       ItemDef("letter",       "信件",     "手写的一封短信，字迹工整，未署名",               1),
    "bread":        ItemDef("bread",        "面包",     "苔老板烤的香喷喷面包，出炉时香味飘半条街",       2),
    "herb":         ItemDef("herb",         "草药",     "小翠亲手配的疗愈草药",                          2),
    "honey":        ItemDef("honey",        "蜂蜜",     "森林深处采来的野生蜂蜜，甜而不腻",              2),
    "cookie":       ItemDef("cookie",       "饼干",     "苔老板烤的幸运饼干，里头夹着一张字条",          2),
    "potion_tea":   ItemDef("potion_tea",   "草药茶",   "小翠秘方熬制，喝一口精神百倍",                  2),
    "map_piece":    ItemDef("map_piece",    "地图碎片", "小蓝旅途中收集的地图残片，不知通向何方",         2),
    "meat":         ItemDef("meat",         "肉块",     "新鲜的肉块，烤一下味道极佳",                    2),
    "noodle":       ItemDef("noodle",       "汤面",     "热腾腾的汤面，配几片野葱",                      2),
    "octopus":      ItemDef("octopus",      "章鱼",     "海边捞上来的小章鱼，老咸最爱",                  2),
    "gourd":        ItemDef("gourd",        "葫芦",     "晒干的葫芦，可以盛水也可以当摆件",              2),
    # ── 资源（base 1-3）──────────────────────
    "branch":       ItemDef("branch",       "树枝",     "随手捡的干树枝，能生火",                        1),
    "rock":         ItemDef("rock",         "石头",     "形状不错的小石头",                              1),
    "bar_copper":   ItemDef("bar_copper",   "铜锭",     "锻造用的铜锭，沉甸甸的",                        2),
    "gem_green":    ItemDef("gem_green",    "翡翠",     "森林深处偶然发现的绿色宝石",                    3),
    "gem_red":      ItemDef("gem_red",      "红宝石",   "罕见的红色宝石，温热似有生命",                  3),
    # ── 工具（base 2-3）──────────────────────
    "shovel":       ItemDef("shovel",       "铲子",     "结实的小铲子，挖土挖坑都行",                    2),
    "hoe":          ItemDef("hoe",          "锄头",     "农人常用的锄头，松土翻地",                      2),
    "pickaxe":      ItemDef("pickaxe",      "镐",       "凿石开矿的镐头，刃口锋利",                      3),
    "watering_can": ItemDef("watering_can", "洒水壶",   "浇花用的洒水壶，黄铜色泽温润",                  2),
    "sickle":       ItemDef("sickle",       "镰刀",     "收割草药用的小镰刀",                            2),
    # ── 物件（base 1-3）──────────────────────
    "crystal":      ItemDef("crystal",      "水晶",     "稀有的紫水晶，会发出淡淡的光",                  3),
    "ancient_book": ItemDef("ancient_book", "古书",     "一本泛黄的羊皮古书，记载着失落的传说",          3),
    "compass":      ItemDef("compass",      "老罗盘",   "老咸随身多年的生锈罗盘，指不了北但意义非凡",    3),
    "glow_stone":   ItemDef("glow_stone",   "萤石",     "煊赫身上一块会发光的石头，夜里格外神秘",        3),
    "coin_purse":   ItemDef("coin_purse",   "钱袋",     "鼓鼓囊囊的小钱袋",                              2),
    "dice":         ItemDef("dice",         "骰子",     "一颗六面骰子，旅行者爱玩",                      1),
    "pan_flute":    ItemDef("pan_flute",    "排笛",     "竹制的小排笛，吹起来婉转动人",                  2),
    # ── 宝物（base 3-5）──────────────────────
    "treasure_small":ItemDef("treasure_small","小宝箱","锁着的小宝箱，里头不知装着什么",                5),
    "gold_cup":     ItemDef("gold_cup",     "金杯",     "做工华丽的金酒杯",                              4),
    "silver_cup":   ItemDef("silver_cup",   "银杯",     "做工精致的银酒杯",                              3),
    "gold_key":     ItemDef("gold_key",     "金钥匙",   "古老的金色钥匙，能开什么锁？",                  4),
    "silver_key":   ItemDef("silver_key",   "银钥匙",   "做工精细的银色钥匙",                            3),
    # ── 卷轴（base 1-3）──────────────────────
    "scroll_fire":  ItemDef("scroll_fire",  "火卷轴",   "封印着火焰魔法的羊皮卷",                        3),
    "scroll_ice":   ItemDef("scroll_ice",   "冰卷轴",   "封印着寒冰魔法的羊皮卷",                        3),
    "scroll_thunder":ItemDef("scroll_thunder","雷卷轴","封印着雷电魔法的羊皮卷",                        3),
    "scroll_blank": ItemDef("scroll_blank", "空白卷轴","什么都没写的羊皮卷，等待书写",                  1),
    # ── 药水（base 1-3）──────────────────────
    "water_pot":    ItemDef("water_pot",    "水壶",     "装满清水的小陶壶",                              1),
    "heart_pot":    ItemDef("heart_pot",    "红心瓶",   "装着红色液体的小瓶，据说能治愈心情",            3),
    "medi_pack":    ItemDef("medi_pack",    "药包",     "急救用的小药包，有点擦伤可以贴一下",            2),
}


def get(item_id: str) -> ItemDef | None:
    return _ITEMS.get(item_id)


def all_items() -> List[ItemDef]:
    return list(_ITEMS.values())


def all_ids() -> List[str]:
    return list(_ITEMS.keys())
