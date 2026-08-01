"""物品定义（前后端共用 ID）。

每件物品有：
  - id        : 标识（前后端 = 资源文件名）
  - name      : 中文名（写进 prompt）
  - desc      : 一句描述（写进 prompt）
  - base_value: 礼物基础价值（决定 delta 量级）

base_value 与好感度 delta 的关系（最终公式见 gifts.py）：
    delta = round(base_value × pref_mult × affection_mult × fatigue_mult)

base_value 分档（按「玩家拿到它有多难」定价，而非名字听着贵不贵）：
  1-3   地图可捡物 / 自产日常。满地都是，送再多也刷不出高好感。
  4-6   NPC 普通回礼、任务常规奖励。地上捡不到，得靠互动换。
  7-9   NPC 中级回礼。要养到一定好感才拿得到。
  10-15 NPC 稀有回礼 / 顶级珍宝。love 档专属，全镇最硬的通货。

铁律：地图上能捡到的东西，base_value 一律 ≤3。
否则玩家蹲点刷可捡物就能堆爆好感，回礼与任务链全部失去意义。
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
    # ── 食物 ──────────────────────
    # 地图上有刷新点的，一律 ≤3
    "flower":       ItemDef("flower",       "野花",     "森林边随手摘的小野花，廉价但有心意",            1),
    "feather":      ItemDef("feather",      "羽毛",     "鸟儿掉下的彩色羽毛，轻盈漂亮",                  1),
    "mushroom":     ItemDef("mushroom",     "蘑菇",     "森林里采的可食用蘑菇",                          1),
    "fish":         ItemDef("fish",         "鲜鱼",     "刚从溪水里抓的活鱼",                            1),
    "acorn":        ItemDef("acorn",        "橡果",     "树下捡的饱满橡果，秋天的味道",                   1),
    "bread":        ItemDef("bread",        "面包",     "苔老板烤的香喷喷面包，出炉时香味飘半条街",       2),
    "herb":         ItemDef("herb",         "草药",     "小翠亲手配的疗愈草药",                          2),
    # 以下地上捡不到，得从 NPC 手里换
    "letter":       ItemDef("letter",       "信件",     "手写的一封短信，字迹工整，未署名",              4),
    "honey":        ItemDef("honey",        "蜂蜜",     "森林深处采来的野生蜂蜜，甜而不腻",              6),
    "cookie":       ItemDef("cookie",       "饼干",     "苔老板烤的幸运饼干，里头夹着一张字条",          5),
    "potion_tea":   ItemDef("potion_tea",   "草药茶",   "小翠秘方熬制，喝一口精神百倍",                  5),
    "map_piece":    ItemDef("map_piece",    "地图碎片", "小蓝旅途中收集的地图残片，不知通向何方",        5),
    "meat":         ItemDef("meat",         "肉块",     "新鲜的肉块，烤一下味道极佳",                    4),
    "noodle":       ItemDef("noodle",       "汤面",     "热腾腾的汤面，配几片野葱",                      4),
    "octopus":      ItemDef("octopus",      "章鱼",     "海边捞上来的小章鱼，老咸最爱",                  6),
    "gourd":        ItemDef("gourd",        "葫芦",     "晒干的葫芦，可以盛水也可以当摆件",              6),
    # ── 资源 ──────────────────────
    "branch":       ItemDef("branch",       "树枝",     "随手捡的干树枝，能生火",                        1),
    "rock":         ItemDef("rock",         "石头",     "形状不错的小石头",                              1),
    "bar_copper":   ItemDef("bar_copper",   "铜锭",     "锻造用的铜锭，沉甸甸的",                        4),
    # 宝石地上捡不到，是稀有回礼档的硬通货
    "gem_green":    ItemDef("gem_green",    "翡翠",     "森林深处偶然发现的绿色宝石",                   12),
    "gem_red":      ItemDef("gem_red",      "红宝石",   "罕见的红色宝石，温热似有生命",                 12),
    # ── 工具 ──────────────────────
    "shovel":       ItemDef("shovel",       "铲子",     "结实的小铲子，挖土挖坑都行",                    4),
    "hoe":          ItemDef("hoe",          "锄头",     "农人常用的锄头，松土翻地",                      4),
    "pickaxe":      ItemDef("pickaxe",      "镐",       "凿石开矿的镐头，刃口锋利",                      5),
    "watering_can": ItemDef("watering_can", "洒水壶",   "浇花用的洒水壶，黄铜色泽温润",                  4),
    "sickle":       ItemDef("sickle",       "镰刀",     "收割草药用的小镰刀",                            4),
    # ── 物件 ──────────────────────
    # 水晶 / 古书地图上有刷新点，按铁律压在 3
    "crystal":      ItemDef("crystal",      "水晶",     "稀有的紫水晶，会发出淡淡的光",                  3),
    "ancient_book": ItemDef("ancient_book", "古书",     "一本泛黄的羊皮古书，记载着失落的传说",          3),
    # 以下地上捡不到，只能从 NPC 手里换：中级回礼档
    "compass":      ItemDef("compass",      "老罗盘",   "老咸随身多年的生锈罗盘，指不了北但意义非凡",    8),
    "glow_stone":   ItemDef("glow_stone",   "萤石",     "煊赫身上一块会发光的石头，夜里格外神秘",        8),
    "coin_purse":   ItemDef("coin_purse",   "钱袋",     "鼓鼓囊囊的小钱袋",                              5),
    "dice":         ItemDef("dice",         "骰子",     "一颗六面骰子，旅行者爱玩",                      4),
    "pan_flute":    ItemDef("pan_flute",    "排笛",     "竹制的小排笛，吹起来婉转动人",                  5),
    # ── 宝物（顶级珍宝，love 档回礼 / 高阶任务奖励）──
    "treasure_small":ItemDef("treasure_small","小宝箱","锁着的小宝箱，里头不知装着什么",               15),
    "gold_cup":     ItemDef("gold_cup",     "金杯",     "做工华丽的金酒杯",                             12),
    "silver_cup":   ItemDef("silver_cup",   "银杯",     "做工精致的银酒杯",                              7),
    "gold_key":     ItemDef("gold_key",     "金钥匙",   "古老的金色钥匙，能开什么锁？",                 12),
    "silver_key":   ItemDef("silver_key",   "银钥匙",   "做工精细的银色钥匙",                            7),
    # ── 卷轴 ──────────────────────
    "scroll_fire":  ItemDef("scroll_fire",  "火卷轴",   "封印着火焰魔法的羊皮卷",                       10),
    "scroll_ice":   ItemDef("scroll_ice",   "冰卷轴",   "封印着寒冰魔法的羊皮卷",                       10),
    "scroll_thunder":ItemDef("scroll_thunder","雷卷轴","封印着雷电魔法的羊皮卷",                       10),
    "scroll_blank": ItemDef("scroll_blank", "空白卷轴","什么都没写的羊皮卷，等待书写",                  4),
    # ── 药水 ──────────────────────
    "water_pot":    ItemDef("water_pot",    "水壶",     "装满清水的小陶壶",                              4),
    "heart_pot":    ItemDef("heart_pot",    "红心瓶",   "装着红色液体的小瓶，据说能治愈心情",           10),
    "medi_pack":    ItemDef("medi_pack",    "药包",     "急救用的小药包，有点擦伤可以贴一下",            5),

    # ── 地图可捡物（base 1-3，满地都是，不能靠它刷好感）──
    "shell":        ItemDef("shell",        "贝壳",     "湖边捡的螺旋形贝壳，里面像有海声",              2),
    "berry":        ItemDef("berry",        "浆果",     "草丛里采的酸甜浆果",                            1),
    "mint":         ItemDef("mint",         "薄荷叶",   "新鲜薄荷叶，清香扑鼻",                          2),
    "dewdrop":      ItemDef("dewdrop",      "露珠",     "清晨叶上凝成的圆润露水珠，闪闪的",              2),
    "pearl":        ItemDef("pearl",        "珍珠",     "湖底罕见的圆润珍珠",                            3),
    "resin":        ItemDef("resin",        "树脂",     "古树滴下的琥珀色树脂，温润有光",                3),
    "moonstone":    ItemDef("moonstone",    "月光石",   "夜里会发出淡淡冷光的石头",                      3),
    "bark":         ItemDef("bark",         "树皮",     "从古树上自然脱落的一片树皮",                    2),
}


def get(item_id: str) -> ItemDef | None:
    return _ITEMS.get(item_id)


def all_items() -> List[ItemDef]:
    return list(_ITEMS.values())


def all_ids() -> List[str]:
    return list(_ITEMS.keys())
