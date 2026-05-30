@tool
class_name GroundLayer
extends TileMapLayer
## 地形铺设层。
##
## 【重要】_paint() 只在编辑器里运行（用于初始铺底或 rebuild 按钮刷新）。
## 游戏运行时直接使用 .tscn 保存的 tile_map_data，手画的湖泊/特殊地形不会被覆盖。
## 编辑器里点 rebuild 按钮可以重铺草地底层（只填空白格，不覆盖已有 tile）。

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

## 编辑器一键重铺按钮（只填没有 tile 的空格，不清除已画内容）
@export var rebuild: bool = false:
	set(value):
		rebuild = false
		if Engine.is_editor_hint():
			_paint()


func _ready() -> void:
	# 地面层永远在所有角色/建筑下方
	z_index = -200
	# 游戏运行时不重铺，直接用 .tscn 保存的 tile_map_data
	pass


func _paint() -> void:
	## 只填空白格（不覆盖已有 tile），保护手画的湖/路等内容
	if tile_set == null:
		push_warning("GroundLayer: tile_set 未设置，无法铺地")
		return
	var filled := 0
	for x in range(width):
		for y in range(height):
			var cell: Vector2i = origin_offset + Vector2i(x, y)
			# 已有 tile 的格子跳过（保护手画内容）
			if get_cell_source_id(cell) != -1:
				continue
			set_cell(cell, grass_source, grass_atlas)
			filled += 1
	print("[GroundLayer] 补填了 %d 个空白草地 tile" % filled)
