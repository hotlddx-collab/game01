extends Node
## 物品定义数据库（autoload）
##
## 全局共用的物品 ID → 名称/icon/base_value 字典。
## item_id 与 agent_server/items.py 一一对应。

const ITEMS := {
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
	"bread": {
		"name": "面包",
		"desc": "苔老板烤的香喷喷面包",
		"icon": "res://res/ninja_adventure/Items/Food/Onigiri.png",
		"base_value": 6,
	},
	"herb": {
		"name": "草药",
		"desc": "崔草草配的疗愈草药",
		"icon": "res://res/ninja_adventure/Items/Resource/Grass.png",
		"base_value": 7,
	},
	"crystal": {
		"name": "水晶",
		"desc": "稀有的紫水晶，会发光",
		"icon": "res://res/ninja_adventure/Items/Resource/GemPurple.png",
		"base_value": 12,
	},
	"ancient_book": {
		"name": "古书",
		"desc": "一本泛黄的羊皮古书，记载着失落的传说",
		"icon": "res://res/ninja_adventure/Items/Object/Book.png",
		"base_value": 15,
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
