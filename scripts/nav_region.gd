extends NavigationRegion2D
## 动态导航区域。
##
## 运行时自动从 GroundLayer 范围 + ObstacleLayer tile 数据生成可行走多边形，
## 无需在编辑器里手动烘焙。地图修改后重启游戏即更新。
##
## 场景里加一个 NavigationRegion2D 节点，挂此脚本，其他什么都不用配置。

## 地图可行走区域（像素坐标）— 与 GroundLayer 的 origin_offset + size 匹配
@export var walk_min: Vector2 = Vector2(-320, -320)  # tile(-20,-20) * 16
@export var walk_max: Vector2 = Vector2(1600, 1120)  # tile(100,70) * 16

## 每个 tile 边长（像素）
@export var tile_size: int = 16

## 障碍膨胀量（像素）：让 NPC 离障碍物边缘保持一段距离
@export var obstacle_margin: float = 4.0


func _ready() -> void:
	if Engine.is_editor_hint():
		return
	# 等两帧让所有 TileMapLayer 节点 _ready() 完成加载 tile 数据
	await get_tree().process_frame
	await get_tree().process_frame
	_build_and_bake()


func _build_and_bake() -> void:
	var poly := NavigationPolygon.new()

	# ── 外边界：整个可行走区域（顺时针，Godot Y向下）──
	var outer := PackedVector2Array([
		Vector2(walk_min.x, walk_min.y),
		Vector2(walk_max.x, walk_min.y),
		Vector2(walk_max.x, walk_max.y),
		Vector2(walk_min.x, walk_max.y),
	])
	poly.add_outline(outer)

	# ── 障碍孔洞：从 ObstacleLayer 读取 tile 格并合并为矩形 ──
	var obstacle_node := _find_obstacle_layer()
	if obstacle_node:
		var cells: Array = obstacle_node.get_used_cells()
		if not cells.is_empty():
			var rects := _merge_cells(cells)
			var m := obstacle_margin
			for rect: Rect2i in rects:
				var rx: float = rect.position.x * tile_size - m
				var ry: float = rect.position.y * tile_size - m
				var rw: float = rect.size.x * tile_size + m * 2.0
				var rh: float = rect.size.y * tile_size + m * 2.0
				# 孔洞逆时针（与外边界方向相反）
				poly.add_outline(PackedVector2Array([
					Vector2(rx,      ry),
					Vector2(rx,      ry + rh),
					Vector2(rx + rw, ry + rh),
					Vector2(rx + rw, ry),
				]))

	poly.make_polygons_from_outlines()
	navigation_polygon = poly
	bake_navigation_polygon(false)

	var hole_count := poly.get_outline_count() - 1
	print("[NavRegion] 导航网格烘焙完成，障碍孔洞数：%d" % hole_count)


func _find_obstacle_layer() -> TileMapLayer:
	# 先找 group，找不到再用节点名扫描
	var groups := get_tree().get_nodes_in_group("obstacle_layer")
	if not groups.is_empty():
		return groups[0] as TileMapLayer
	# fallback：遍历父节点的子节点
	var parent := get_parent()
	if parent == null:
		return null
	for child in parent.get_children():
		if child is TileMapLayer and child.name == "Obstacle":
			return child as TileMapLayer
	return null


func _merge_cells(cells: Array) -> Array[Rect2i]:
	## 贪心合并相邻 tile 为大矩形（减少多边形孔洞数）
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
				if not grid.has(Vector2i(c.x + dx, c.y + h)) or visited.has(Vector2i(c.x + dx, c.y + h)):
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
