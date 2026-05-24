@tool
class_name GroundLayer
extends TileMapLayer
## 地形铺设层。
##
## 启动时自动铺草地（基于 @export 参数）。
## 编辑器里点 rebuild 按钮可以即时重铺看效果。
##
## 后期可扩展：撒树/花/路径，加 noise 让草地有变化。

## 地图覆盖宽度（tile 数）
@export_range(20, 300, 1) var width: int = 100
## 地图覆盖高度
@export_range(20, 300, 1) var height: int = 70
## 起点偏移（tile 坐标）
@export var origin_offset: Vector2i = Vector2i(-20, -20)

## 草地 tile 来源 id（在 world.tres TileSet 里的 source_id）
@export var grass_source: int = 0
## 草地 atlas 坐标
@export var grass_atlas: Vector2i = Vector2i(1, 4)

## 编辑器一键重铺按钮（点了立刻铺，再次点也行）
@export var rebuild: bool = false:
	set(value):
		rebuild = false
		_paint()


func _ready() -> void:
	# 启动时自动铺一次（包括编辑器和运行时）
	_paint()


func _paint() -> void:
	if tile_set == null:
		push_warning("GroundLayer: tile_set 未设置，无法铺地")
		return
	clear()
	var count := 0
	for x in range(width):
		for y in range(height):
			var cell: Vector2i = origin_offset + Vector2i(x, y)
			set_cell(cell, grass_source, grass_atlas)
			count += 1
	if Engine.is_editor_hint():
		print("[GroundLayer] 铺了 %d 个草地 tile" % count)
