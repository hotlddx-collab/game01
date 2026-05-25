@tool
class_name DecorationLayer
extends TileMapLayer
## 装饰层（树/花/草）。
##
## 在地图上随机散布 1×1 的小花、小草、蘑菇等点缀，
## 自动避开路径走廊和建筑占位。
##
## 用确定性 RNG（seed 固定）保证每次跑一样。

## 装饰 tile palette：source=1 (TilesetNature)，全是 1×1
@export var decoration_source: int = 1
@export var decoration_atlas_pool: Array[Vector2i] = [
	Vector2i(0, 10),  # 小绿草
	Vector2i(1, 10),  # 小绿草变体
	Vector2i(2, 10),  # 小绿草变体
	Vector2i(5, 10),  # 草丛
	Vector2i(0, 11),  # 黄花
	Vector2i(1, 11),  # 黄花丛
	Vector2i(3, 11),  # 红花
]

## 散布范围（tile 坐标）
@export var spawn_rect_min: Vector2i = Vector2i(-15, -15)
@export var spawn_rect_max: Vector2i = Vector2i(75, 45)
## 总数
@export_range(20, 500, 5) var count: int = 150
## 随机种子（固定 = 每次布局一样）
@export var rng_seed: int = 42

## 排除区域：建筑 + 4 条路径（tile 坐标，含 1 格 buffer）
## 改动 path_layer.gd 里建筑/广场坐标的话，这里也要同步
@export var exclude_rects: Array[Rect2i] = [
	# 建筑（5 个）
	Rect2i(10, 11, 5, 4),     # HomeBear
	Rect2i(60, 11, 5, 4),     # HomeFox
	Rect2i(23, 29, 5, 4),     # Bakery
	Rect2i(48, 29, 6, 4),     # PostOffice
	Rect2i(33, 19, 9, 6),     # Plaza
	# 路径（横走廊 + 4 条竖腿，width 2 + 1 buffer）
	Rect2i(11, 20, 52, 4),    # 横向主走廊（HomeBear x12 到 HomeFox x62）
	Rect2i(10, 12, 5, 12),    # HomeBear 竖腿（x12 周围 ±2，y 12-22）
	Rect2i(60, 12, 5, 12),    # HomeFox 竖腿
	Rect2i(23, 21, 5, 11),    # Bakery 竖腿
	Rect2i(48, 21, 5, 11),    # PostOffice 竖腿
]

@export var rebuild: bool = false:
	set(value):
		rebuild = false
		_paint()


func _ready() -> void:
	_paint()


func _paint() -> void:
	if tile_set == null:
		return
	if decoration_atlas_pool.is_empty():
		return
	clear()
	var rng := RandomNumberGenerator.new()
	rng.seed = rng_seed

	var placed := 0
	var attempts := 0
	while placed < count and attempts < count * 10:
		attempts += 1
		var x := rng.randi_range(spawn_rect_min.x, spawn_rect_max.x)
		var y := rng.randi_range(spawn_rect_min.y, spawn_rect_max.y)
		var cell := Vector2i(x, y)
		if _is_excluded(cell):
			continue
		var atlas: Vector2i = decoration_atlas_pool[rng.randi() % decoration_atlas_pool.size()]
		set_cell(cell, decoration_source, atlas)
		placed += 1


func _is_excluded(cell: Vector2i) -> bool:
	for rect in exclude_rects:
		if rect.has_point(cell):
			return true
	return false
