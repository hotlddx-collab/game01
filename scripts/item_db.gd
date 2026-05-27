extends Node
## 物品定义数据库（autoload）
##
## 全局共用的物品 ID → 名称/icon/base_value 字典。
## item_id 与 agent_server/items.py 一一对应。

const ITEMS := {
	# ── 低价值 ────────────────────────────────
	"flower": {
		"name": "野花",
		"desc": "森林边随手摘的小野花，廉价但有心意",
		"icon": "res://res/ninja_adventure/Items/Food/Seed1.png",
		"base_value": 3,
	},
	"feather": {
		"name": "羽毛",
		"desc": "鸟儿掉下的彩色羽毛，轻盈漂亮",
		"icon": "res://res/ninja_adventure/Items/Resource/feather.png",
		"base_value": 4,
	},
	"mushroom": {
		"name": "蘑菇",
		"desc": "森林里采的可食用蘑菇",
		"icon": "res://res/ninja_adventure/Items/Food/Nut.png",
		"base_value": 5,
	},
	"fish": {
		"name": "鲜鱼",
		"desc": "刚从溪水里抓的活鱼",
		"icon": "res://res/ninja_adventure/Items/Food/Fish.png",
		"base_value": 5,
	},
	"acorn": {
		"name": "橡果",
		"desc": "树下捡的饱满橡果，秋天的味道",
		"icon": "res://res/ninja_adventure/Items/Food/Nut2.png",
		"base_value": 4,
	},
	"letter": {
		"name": "信件",
		"desc": "手写的一封短信，字迹工整，未署名",
		"icon": "res://res/ninja_adventure/Items/Scroll/Scroll.png",
		"base_value": 4,
	},
	# ── 中价值 ────────────────────────────────
	"bread": {
		"name": "面包",
		"desc": "苔老板烤的香喷喷面包，出炉时香味飘半条街",
		"icon": "res://res/ninja_adventure/Items/Food/Onigiri.png",
		"base_value": 6,
	},
	"herb": {
		"name": "草药",
		"desc": "小翠亲手配的疗愈草药",
		"icon": "res://res/ninja_adventure/Items/Resource/Grass.png",
		"base_value": 7,
	},
	"honey": {
		"name": "蜂蜜",
		"desc": "森林深处采来的野生蜂蜜，甜而不腻",
		"icon": "res://res/ninja_adventure/Items/Food/Honey.png",
		"base_value": 6,
	},
	"cookie": {
		"name": "饼干",
		"desc": "苔老板烤的幸运饼干，里头夹着一张字条",
		"icon": "res://res/ninja_adventure/Items/Food/FortuneCookie.png",
		"base_value": 6,
	},
	"potion_tea": {
		"name": "草药茶",
		"desc": "小翠秘方熬制，喝一口精神百倍",
		"icon": "res://res/ninja_adventure/Items/Potion/MilkPot.png",
		"base_value": 7,
	},
	"map_piece": {
		"name": "地图碎片",
		"desc": "小蓝旅途中收集的地图残片，不知通向何方",
		"icon": "res://res/ninja_adventure/Items/Scroll/ScrollRock.png",
		"base_value": 6,
	},
	# ── 高价值 ────────────────────────────────
	"crystal": {
		"name": "水晶",
		"desc": "稀有的紫水晶，会发出淡淡的光",
		"icon": "res://res/ninja_adventure/Items/Resource/GemPurple.png",
		"base_value": 12,
	},
	"ancient_book": {
		"name": "古书",
		"desc": "一本泛黄的羊皮古书，记载着失落的传说",
		"icon": "res://res/ninja_adventure/Items/Object/Book.png",
		"base_value": 15,
	},
	"compass": {
		"name": "老罗盘",
		"desc": "老咸随身多年的生锈罗盘，指不了北但意义非凡",
		"icon": "res://res/ninja_adventure/Items/Object/Hourglass.png",
		"base_value": 12,
	},
	"glow_stone": {
		"name": "萤石",
		"desc": "煊赫身上一块会发光的石头，夜里格外神秘",
		"icon": "res://res/ninja_adventure/Items/Resource/GemYellow.png",
		"base_value": 12,
	},
	# ── 食物（新增）────────────────────────────
	"meat": {
		"name": "肉块",
		"desc": "新鲜的肉块，烤一下味道极佳",
		"icon": "res://res/ninja_adventure/Items/Food/Meat.png",
		"base_value": 6,
	},
	"noodle": {
		"name": "汤面",
		"desc": "热腾腾的汤面，配几片野葱",
		"icon": "res://res/ninja_adventure/Items/Food/Noodle.png",
		"base_value": 6,
	},
	"octopus": {
		"name": "章鱼",
		"desc": "海边捞上来的小章鱼，老咸最爱",
		"icon": "res://res/ninja_adventure/Items/Food/Octopus.png",
		"base_value": 6,
	},
	"gourd": {
		"name": "葫芦",
		"desc": "晒干的葫芦，可以盛水也可以当摆件",
		"icon": "res://res/ninja_adventure/Items/Object/Gourd.png",
		"base_value": 6,
	},
	# ── 资源 ──────────────────────────────────
	"branch": {
		"name": "树枝",
		"desc": "随手捡的干树枝，能生火",
		"icon": "res://res/ninja_adventure/Items/Resource/Branch.png",
		"base_value": 3,
	},
	"rock": {
		"name": "石头",
		"desc": "形状不错的小石头",
		"icon": "res://res/ninja_adventure/Items/Resource/Rock.png",
		"base_value": 3,
	},
	"bar_copper": {
		"name": "铜锭",
		"desc": "锻造用的铜锭，沉甸甸的",
		"icon": "res://res/ninja_adventure/Items/Resource/BarCopper.png",
		"base_value": 7,
	},
	"gem_green": {
		"name": "翡翠",
		"desc": "森林深处偶然发现的绿色宝石",
		"icon": "res://res/ninja_adventure/Items/Resource/GemGreen.png",
		"base_value": 12,
	},
	"gem_red": {
		"name": "红宝石",
		"desc": "罕见的红色宝石，温热似有生命",
		"icon": "res://res/ninja_adventure/Items/Resource/GemRed.png",
		"base_value": 12,
	},
	# ── 工具 ──────────────────────────────────
	"shovel": {
		"name": "铲子",
		"desc": "结实的小铲子，挖土挖坑都行",
		"icon": "res://res/ninja_adventure/Items/Tool/Shovel.png",
		"base_value": 7,
	},
	"hoe": {
		"name": "锄头",
		"desc": "农人常用的锄头，松土翻地",
		"icon": "res://res/ninja_adventure/Items/Tool/Hoe.png",
		"base_value": 7,
	},
	"pickaxe": {
		"name": "镐",
		"desc": "凿石开矿的镐头，刃口锋利",
		"icon": "res://res/ninja_adventure/Items/Tool/Pickaxe.png",
		"base_value": 12,
	},
	"watering_can": {
		"name": "洒水壶",
		"desc": "浇花用的洒水壶，黄铜色泽温润",
		"icon": "res://res/ninja_adventure/Items/Tool/WateringCan.png",
		"base_value": 7,
	},
	"sickle": {
		"name": "镰刀",
		"desc": "收割草药用的小镰刀",
		"icon": "res://res/ninja_adventure/Items/Tool/Sickle.png",
		"base_value": 7,
	},
	# ── 物件（新增）────────────────────────────
	"coin_purse": {
		"name": "钱袋",
		"desc": "鼓鼓囊囊的小钱袋",
		"icon": "res://res/ninja_adventure/Items/Object/MoneyBag.png",
		"base_value": 7,
	},
	"dice": {
		"name": "骰子",
		"desc": "一颗六面骰子，旅行者爱玩",
		"icon": "res://res/ninja_adventure/Items/Object/Dice 6.png",
		"base_value": 4,
	},
	"pan_flute": {
		"name": "排笛",
		"desc": "竹制的小排笛，吹起来婉转动人",
		"icon": "res://res/ninja_adventure/Items/Object/PanFlute.png",
		"base_value": 7,
	},
	# ── 宝物 ──────────────────────────────────
	"treasure_small": {
		"name": "小宝箱",
		"desc": "锁着的小宝箱，里头不知装着什么",
		"icon": "res://res/ninja_adventure/Items/Treasure/LittleTreasureChest.png",
		"base_value": 18,
	},
	"gold_cup": {
		"name": "金杯",
		"desc": "做工华丽的金酒杯",
		"icon": "res://res/ninja_adventure/Items/Treasure/GoldCup.png",
		"base_value": 14,
	},
	"silver_cup": {
		"name": "银杯",
		"desc": "做工精致的银酒杯",
		"icon": "res://res/ninja_adventure/Items/Treasure/SilverCup.png",
		"base_value": 10,
	},
	"gold_key": {
		"name": "金钥匙",
		"desc": "古老的金色钥匙，能开什么锁？",
		"icon": "res://res/ninja_adventure/Items/Treasure/GoldKey.png",
		"base_value": 14,
	},
	"silver_key": {
		"name": "银钥匙",
		"desc": "做工精细的银色钥匙",
		"icon": "res://res/ninja_adventure/Items/Treasure/SilverKey.png",
		"base_value": 10,
	},
	# ── 卷轴 ──────────────────────────────────
	"scroll_fire": {
		"name": "火卷轴",
		"desc": "封印着火焰魔法的羊皮卷",
		"icon": "res://res/ninja_adventure/Items/Scroll/ScrollFire.png",
		"base_value": 12,
	},
	"scroll_ice": {
		"name": "冰卷轴",
		"desc": "封印着寒冰魔法的羊皮卷",
		"icon": "res://res/ninja_adventure/Items/Scroll/ScrollIce.png",
		"base_value": 12,
	},
	"scroll_thunder": {
		"name": "雷卷轴",
		"desc": "封印着雷电魔法的羊皮卷",
		"icon": "res://res/ninja_adventure/Items/Scroll/ScrollThunder.png",
		"base_value": 12,
	},
	"scroll_blank": {
		"name": "空白卷轴",
		"desc": "什么都没写的羊皮卷，等待书写",
		"icon": "res://res/ninja_adventure/Items/Scroll/ScrollEmpty.png",
		"base_value": 3,
	},
	# ── 药水 ──────────────────────────────────
	"water_pot": {
		"name": "水壶",
		"desc": "装满清水的小陶壶",
		"icon": "res://res/ninja_adventure/Items/Potion/WaterPot.png",
		"base_value": 3,
	},
	"heart_pot": {
		"name": "红心瓶",
		"desc": "装着红色液体的小瓶，据说能治愈心情",
		"icon": "res://res/ninja_adventure/Items/Potion/Heart.png",
		"base_value": 12,
	},
	"medi_pack": {
		"name": "药包",
		"desc": "急救用的小药包，有点擦伤可以贴一下",
		"icon": "res://res/ninja_adventure/Items/Potion/Medipack.png",
		"base_value": 7,
	},
}


func has(item_id: String) -> bool:
	return ITEMS.has(item_id)


func get_def(item_id: String) -> Dictionary:
	return ITEMS.get(item_id, {})


func get_item_name(item_id: String) -> String:
	return ITEMS.get(item_id, {}).get("name", item_id)


func get_icon(item_id: String) -> Texture2D:
	var path: String = ITEMS.get(item_id, {}).get("icon", "")
	if path == "" or not ResourceLoader.exists(path):
		return null
	return load(path)


func get_base_value(item_id: String) -> int:
	return int(ITEMS.get(item_id, {}).get("base_value", 0))


func all_ids() -> Array:
	return ITEMS.keys()
