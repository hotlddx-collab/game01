@tool
class_name ObstacleLayer
extends TileMapLayer
## 障碍图层 — 画了 tile 的格子自动生成碰撞体。
##
## 用法：
## 1. 在 main.tscn 加一个 TileMapLayer 节点，把这个脚本挂上
## 2. 在编辑器里随便画 tile（湖、岩石、围墙等），都成阻挡
## 3. 运行游戏即生效，玩家/NPC 走不进去
##
## 注意：此层 tile 的视觉也会显示。如果只想要不可见碰撞，用透明 tile 即可。
## 通常推荐：在 Ground 层画湖泊视觉，再在此层画"碰撞用 tile"覆盖（视觉一致即可）。

## 碰撞层位（默认与玩家碰撞）
@export_flags_2d_physics var collision_layer_mask: int = 1
@export_flags_2d_physics var collision_mask_value: int = 0


func _ready() -> void:
	# 此层 z_index 跟随 ground，避免遮挡角色
	z_index = -190
	if Engine.is_editor_hint():
		return
	_build_collision()


func _build_collision() -> void:
	# 清掉旧碰撞（重启场景时）
	for child in get_children():
		if child is StaticBody2D:
			child.queue_free()

	if tile_set == null:
		return

	var ts: float = float(tile_set.tile_size.x)  # 通常 16
	var cells := get_used_cells()
	if cells.is_empty():
		return

	# 用贪心算法把连续 tile 合并为大矩形，减少碰撞体数量
	var rects := _merge_cells_to_rects(cells)

	for rect in rects:
		var body := StaticBody2D.new()
		body.collision_layer = collision_layer_mask
		body.collision_mask = collision_mask_value
		add_child(body)

		var shape := CollisionShape2D.new()
		var rect_shape := RectangleShape2D.new()
		rect_shape.size = Vector2(rect.size.x * ts, rect.size.y * ts)
		shape.shape = rect_shape
		# tile 坐标转像素，定位到矩形中心
		var center_x: float = (rect.position.x + rect.size.x * 0.5) * ts
		var center_y: float = (rect.position.y + rect.size.y * 0.5) * ts
		shape.position = Vector2(center_x, center_y)
		body.add_child(shape)

	print("[ObstacleLayer] 生成 %d 个碰撞体（覆盖 %d 个 tile）" % [rects.size(), cells.size()])


func _merge_cells_to_rects(cells: Array) -> Array[Rect2i]:
	## 贪心合并：横向扫一遍连续行，再纵向合并相同范围的行
	var grid := {}
	for c: Vector2i in cells:
		grid[c] = true

	var rects: Array[Rect2i] = []
	var visited := {}

	for c: Vector2i in cells:
		if visited.has(c):
			continue
		# 横向扩展
		var w := 1
		while grid.has(Vector2i(c.x + w, c.y)) and not visited.has(Vector2i(c.x + w, c.y)):
			w += 1
		# 纵向扩展（要求每行同样宽度）
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
		# 标记 visited
		for dx in range(w):
			for dy in range(h):
				visited[Vector2i(c.x + dx, c.y + dy)] = true
		rects.append(Rect2i(c.x, c.y, w, h))

	return rects
