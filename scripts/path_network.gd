class_name PathNetwork
## 路网寻路工具（静态类）。
##
## 按照 data/world/path_network.json 定义的路网节点和连线，
## 为 NPC 规划"起点→路网→终点"的路径序列。
## 使用 A* 在路网节点间寻最短路，保证 NPC 沿预定道路行进。

static var _wps: Dictionary = {}          # id → Vector2
static var _adj: Dictionary = {}          # id → [id, ...]
static var _loaded: bool = false

# ──────────────────────────────────────
# 公开接口
# ──────────────────────────────────────

## 返回从 from_pos 到 to_pos 途径路网的路径点列表（含起止点）。
## 如果路网未载入或无法规划，则退化为 [from_pos, to_pos]。
static func find_path(from_pos: Vector2, to_pos: Vector2) -> Array[Vector2]:
	_ensure_loaded()
	if _wps.is_empty():
		return [from_pos, to_pos]

	# 起点最近路网节点
	var start_wp: String = _nearest(from_pos)
	# 终点最近路网节点
	var end_wp: String = _nearest(to_pos)

	# 起止点同节点 → 直走
	if start_wp == end_wp:
		return [from_pos, to_pos]

	# A* 在路网节点间寻路
	var wp_path: Array[String] = _astar(start_wp, end_wp)
	if wp_path.is_empty():
		return [from_pos, to_pos]

	# 组合最终路径
	var result: Array[Vector2] = [from_pos]
	for wp in wp_path:
		result.append(_wps[wp])
	result.append(to_pos)
	return result


## 获取所有节点名列表（调试用）
static func all_waypoint_ids() -> Array:
	_ensure_loaded()
	return _wps.keys()


## 所有路网节点坐标（保证在道路上、可达）
static func all_waypoint_positions() -> Array[Vector2]:
	_ensure_loaded()
	var out: Array[Vector2] = []
	for id in _wps:
		out.append(_wps[id])
	return out


## 随机一个路网节点坐标（远离 avoid 优先，可达且在陆地）
static func random_point(avoid: Vector2 = Vector2.INF, min_dist: float = 0.0) -> Vector2:
	var pts := all_waypoint_positions()
	if pts.is_empty():
		return Vector2.ZERO
	if avoid != Vector2.INF and min_dist > 0.0:
		var far: Array[Vector2] = []
		for p in pts:
			if p.distance_to(avoid) >= min_dist:
				far.append(p)
		if not far.is_empty():
			pts = far
	return pts[randi() % pts.size()]


# ──────────────────────────────────────
# 内部实现
# ──────────────────────────────────────

static func _ensure_loaded() -> void:
	if _loaded:
		return
	_loaded = true
	const PATH := "res://data/world/path_network.json"
	if not FileAccess.file_exists(PATH):
		push_warning("PathNetwork: 找不到 %s" % PATH)
		return
	var f := FileAccess.open(PATH, FileAccess.READ)
	if f == null:
		return
	var data = JSON.parse_string(f.get_as_text())
	f.close()
	if data == null:
		return

	# 读节点
	for id in data.get("waypoints", {}):
		var v = data["waypoints"][id]
		_wps[id] = Vector2(float(v[0]), float(v[1]))
		_adj[id] = []

	# 读连线（双向）
	for conn in data.get("connections", []):
		if conn.size() < 2:
			continue
		var a: String = conn[0]
		var b: String = conn[1]
		if _adj.has(a) and not _adj[a].has(b):
			_adj[a].append(b)
		if _adj.has(b) and not _adj[b].has(a):
			_adj[b].append(a)

	print("[PathNetwork] 载入 %d 个节点，%d 条连线" % [
		_wps.size(),
		data.get("connections", []).size()
	])


## 找离 pos 最近的路网节点
static func _nearest(pos: Vector2) -> String:
	var best_id: String = ""
	var best_d: float = INF
	for id in _wps:
		var d: float = pos.distance_squared_to(_wps[id])
		if d < best_d:
			best_d = d
			best_id = id
	return best_id


## A*：从 start 到 end，返回节点序列（不含 start，含 end）
static func _astar(start: String, goal: String) -> Array[String]:
	# open_set: {id: {g, f, parent}}
	var open: Dictionary = {}
	var closed: Dictionary = {}

	open[start] = {
		"g": 0.0,
		"f": _wps[start].distance_to(_wps[goal]),
		"parent": ""
	}

	while not open.is_empty():
		# 取 f 最小的节点
		var cur: String = ""
		var cur_f: float = INF
		for id in open:
			if open[id]["f"] < cur_f:
				cur_f = open[id]["f"]
				cur = id

		if cur == goal:
			# 重建路径
			var path: Array[String] = []
			var node := cur
			while node != "" and node != start:
				path.push_front(node)
				node = open.get(node, closed.get(node, {})).get("parent", "")
			return path

		var cur_data: Dictionary = open[cur]
		closed[cur] = cur_data
		open.erase(cur)

		for neighbor in _adj.get(cur, []):
			if closed.has(neighbor):
				continue
			var g: float = cur_data["g"] + _wps[cur].distance_to(_wps[neighbor])
			var f: float = g + _wps[neighbor].distance_to(_wps[goal])
			if not open.has(neighbor) or g < open[neighbor]["g"]:
				open[neighbor] = {"g": g, "f": f, "parent": cur}

	return []  # 无路可走
