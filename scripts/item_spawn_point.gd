@tool
extends Marker2D
class_name ItemSpawnPoint
## 物品生成点（编辑器可见可拖动）
##
## 用法：
##   1. 在 main.tscn 任意位置加 ItemSpawnPoint 节点
##   2. 检查器设置 item_id（哪种物品）
##   3. ItemSpawner 自动扫描这些节点，按 item_id 分组维持物品数量
##
## 编辑器里显示彩色圆圈 + 物品中文名，方便识别和调整。

## 生成什么物品（写 item_id，如 "flower" "shell" "fish"）
@export var item_id: String = "flower":
	set(value):
		item_id = value
		queue_redraw()

## 标签颜色（按物品类型自动着色，也可手动覆盖）
@export var label_color: Color = Color(1, 1, 1, 1):
	set(value):
		label_color = value
		queue_redraw()


# 编辑器里显示的中文名（不依赖 ItemDB 单例，硬编码常用物品）
const ITEM_DISPLAY := {
	"flower": "野花", "feather": "羽毛", "mushroom": "蘑菇", "fish": "鲜鱼",
	"acorn": "橡果", "letter": "信件", "bread": "面包", "herb": "草药",
	"honey": "蜂蜜", "cookie": "饼干", "potion_tea": "草药茶",
	"map_piece": "地图碎片", "meat": "肉块", "noodle": "面条",
	"octopus": "章鱼", "gourd": "葫芦", "branch": "树枝", "rock": "石头",
	"bar_copper": "铜条", "gem_green": "绿宝石", "gem_red": "红宝石",
	"shovel": "铲子", "hoe": "锄头", "pickaxe": "镐子",
	"watering_can": "水壶", "sickle": "镰刀", "crystal": "水晶",
	"ancient_book": "古书", "compass": "罗盘", "glow_stone": "萤石",
	"coin_purse": "钱袋", "dice": "骰子", "pan_flute": "笛",
	"treasure_small": "小宝箱", "gold_cup": "金杯", "silver_cup": "银杯",
	"gold_key": "金钥匙", "silver_key": "银钥匙",
	"scroll_fire": "火卷", "scroll_ice": "冰卷",
	"scroll_thunder": "雷卷", "scroll_blank": "空白卷",
	"water_pot": "水壶", "heart_pot": "红心瓶", "medi_pack": "药包",
	"shell": "贝壳", "berry": "浆果", "mint": "薄荷", "dewdrop": "露珠",
	"pearl": "珍珠", "resin": "树脂", "moonstone": "月光石", "bark": "树皮",
}

# 按类型自动着色
const ITEM_COLOR := {
	"flower": Color(1.0, 0.6, 0.7, 1),    # 粉
	"herb": Color(0.4, 0.9, 0.4, 1),      # 绿
	"mint": Color(0.5, 1.0, 0.6, 1),      # 浅绿
	"berry": Color(0.9, 0.3, 0.5, 1),     # 紫红
	"mushroom": Color(0.7, 0.4, 0.2, 1),  # 棕
	"acorn": Color(0.7, 0.5, 0.2, 1),     # 黄棕
	"feather": Color(0.9, 0.9, 1.0, 1),   # 白
	"branch": Color(0.5, 0.3, 0.1, 1),    # 深棕
	"rock": Color(0.6, 0.6, 0.6, 1),      # 灰
	"bark": Color(0.5, 0.35, 0.2, 1),     # 棕灰
	"fish": Color(0.5, 0.7, 1.0, 1),      # 蓝
	"shell": Color(1.0, 0.85, 0.7, 1),    # 米
	"pearl": Color(0.95, 0.95, 1.0, 1),   # 银白
	"dewdrop": Color(0.7, 0.9, 1.0, 1),   # 浅蓝
	"resin": Color(1.0, 0.7, 0.2, 1),     # 琥珀
	"moonstone": Color(0.85, 0.85, 1.0, 1), # 冷白
	"crystal": Color(0.7, 0.5, 1.0, 1),   # 紫
}


func _ready() -> void:
	if Engine.is_editor_hint():
		queue_redraw()
		return
	add_to_group("item_spawn_point")
	# 游戏运行时不可见
	visible = true  # 仍保留 modulate；但 _draw 只在编辑器画


func _draw() -> void:
	if not Engine.is_editor_hint():
		return
	var c: Color = ITEM_COLOR.get(item_id, Color(1, 1, 0.5, 1))
	# 外圈（深色描边）
	draw_arc(Vector2.ZERO, 9.0, 0.0, TAU, 24, Color(0, 0, 0, 0.7), 2.0)
	# 内填色
	draw_circle(Vector2.ZERO, 7.0, Color(c.r, c.g, c.b, 0.85))
	# 中心十字
	draw_line(Vector2(-3, 0), Vector2(3, 0), Color.BLACK, 1.2)
	draw_line(Vector2(0, -3), Vector2(0, 3), Color.BLACK, 1.2)
	# 物品名标签（彩色描边）
	var display: String = ITEM_DISPLAY.get(item_id, item_id)
	var font := ThemeDB.fallback_font
	var size := 11
	var text_pos := Vector2(-30, 22)
	# 描边（黑色）
	for dx in [-1, 0, 1]:
		for dy in [-1, 0, 1]:
			if dx == 0 and dy == 0: continue
			draw_string(font, text_pos + Vector2(dx, dy), display,
				HORIZONTAL_ALIGNMENT_CENTER, 60, size, Color(0, 0, 0, 0.9))
	draw_string(font, text_pos, display,
		HORIZONTAL_ALIGNMENT_CENTER, 60, size, c)
