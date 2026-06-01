@tool
class_name ObstacleLayer
extends TileMapLayer
## 障碍图层 — 画了 tile 的格子自动生成碰撞体。
##
## collision_y_frac / collision_h_frac 控制碰撞区在 tile 内的位置：
##   湖泊类（lack）推荐：y_frac=0, h_frac=1.0，完整 tile
##   石头类（rock）推荐：y_frac=0.3, h_frac=0.7，偏下贴脚面
##
## merge_cells=true  → 合并相邻格为大矩形（湖泊/大面积障碍推荐）
## merge_cells=false → 每 tile 单独碰撞体（石头/不规则形状推荐）

@export_range(0.0, 1.0, 0.05) var collision_y_frac: float = 0.5
@export_range(0.1, 1.0, 0.05) var collision_h_frac: float = 0.5
## 关闭合并：每格单独碰撞体，形状更精确（石头层推荐设 false）
@export var merge_cells: bool = true


func _ready() -> void:
	z_index = -190
	add_to_group("obstacle_layer")  # 供 NavRegion 动态查找
	if Engine.is_editor_hint():
		return
	_build_collision()


func _build_collision() -> void:
	for child in get_children():
		if child is StaticBody2D:
			child.queue_free()

	if tile_set == null:
		return

	var ts: int = tile_set.tile_size.x  # 通常 16
	var cells := get_used_cells()
	if cells.is_empty():
		return

	# merge_cells=false → 每格单独碰撞体（精确模式）
	var rects: Array[Rect2i] = []
	if merge_cells:
		rects = _merge_cells_to_rects(cells)
	else:
		for c: Vector2i in cells:
			rects.append(Rect2i(c.x, c.y, 1, 1))

	for rect in rects:
		var body := StaticBody2D.new()
		add_child(body)

		var shape := CollisionShape2D.new()
		var rect_shape := RectangleShape2D.new()

		var w_px: float = rect.size.x * ts
		var h_px: float = rect.size.y * ts * collision_h_frac
		rect_shape.size = Vector2(w_px, h_px)
		shape.shape = rect_shape

		# X 中心 = 矩形左边 + 宽/2（像素）
		var cx: float = (rect.position.x + rect.size.x * 0.5) * ts
		# Y 中心 = tile顶部 + 偏移 + 碰撞高/2
		var tile_top_y: float = rect.position.y * ts
		var cy: float = tile_top_y + rect.size.y * ts * collision_y_frac + h_px * 0.5
		shape.position = Vector2(cx, cy)
		body.add_child(shape)

	print("[ObstacleLayer] 生成 %d 个碰撞体（y_frac=%.2f h_frac=%.2f）" % [rects.size(), collision_y_frac, collision_h_frac])


func _merge_cells_to_rects(cells: Array) -> Array[Rect2i]:
	var grid := {}
	for c: Vector2i in cells:
		grid[c] = true
	var rects: Array[Rect2i] = []
	var visited := {}
	for c: Vector2i in cells:
		if visited.has(c):
			continue
		var w := 1
		while grid.has(Vector2i(c.x + w, c.y)) and not visited.has(Vector2i(c.x + w, c.y)):
			w += 1
		var h := 1
		while true:
			var ok := true
			for dx in range(w):
				var p := Vector2i(c.x + dx, c.y + h)
				if not grid.has(p) or visited.has(p):
					ok = false
					break
			if not ok:
				break
			h += 1
		for dx in range(w):
			for dy in range(h):
				visited[Vector2i(c.x + dx, c.y + dy)] = true
		rects.append(Rect2i(c.x, c.y, w, h))
	return rects
