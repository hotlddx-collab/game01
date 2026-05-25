@tool
class_name PathLayer
extends TileMapLayer
## 石板/泥土路径层。
##
## 自动从中心广场画 H 形路径连接 4 栋建筑：
##   Plaza ↔ HomeBear/HomeFox（北面）
##   Plaza ↔ Bakery/PostOffice（南面）
##
## 实际坐标硬编码（基于当前 main.tscn 建筑位置）。
## 后期改成读取 LocationDB 自动生成。

## 路径 tile：source=2 (TilesetFloor)，atlas (16, 8) 纯棕泥土中段（验证全 4 角都是棕色）
@export var path_source: int = 2
@export var path_atlas: Vector2i = Vector2i(16, 8)
## 路径宽度（tile 数）
@export_range(1, 5, 1) var path_width: int = 2

## 路径节点（tile 坐标）
@export var plaza_tile: Vector2i = Vector2i(37, 22)
@export var home_bear_tile: Vector2i = Vector2i(12, 14)
@export var home_fox_tile: Vector2i = Vector2i(62, 14)
@export var bakery_tile: Vector2i = Vector2i(25, 30)
@export var post_office_tile: Vector2i = Vector2i(50, 30)

@export var rebuild: bool = false:
	set(value):
		rebuild = false
		_paint()


func _ready() -> void:
	_paint()


func _paint() -> void:
	if tile_set == null:
		return
	clear()
	# 4 条路径：Plaza → 各建筑（L 形）
	_draw_l_path(plaza_tile, home_bear_tile)
	_draw_l_path(plaza_tile, home_fox_tile)
	_draw_l_path(plaza_tile, bakery_tile)
	_draw_l_path(plaza_tile, post_office_tile)


## 画一条 L 形路径（先水平后垂直）
func _draw_l_path(from: Vector2i, to: Vector2i) -> void:
	# 横向段（先走 x 到目标列）
	var x_start: int = min(from.x, to.x)
	var x_end: int = max(from.x, to.x)
	for x in range(x_start, x_end + 1):
		_paint_band(Vector2i(x, from.y))
	# 纵向段（再走 y 到目标行）
	var y_start: int = min(from.y, to.y)
	var y_end: int = max(from.y, to.y)
	for y in range(y_start, y_end + 1):
		_paint_band(Vector2i(to.x, y))


## 在指定 tile 周围画一个宽度为 path_width 的带
func _paint_band(center: Vector2i) -> void:
	var half: int = path_width / 2
	for dx in range(-half, half + path_width % 2):
		for dy in range(-half, half + path_width % 2):
			set_cell(center + Vector2i(dx, dy), path_source, path_atlas)
