extends Node
## 物品生成器 - 扫描场景中的 ItemSpawnPoint 节点
##
## 工作原理：
##   1. 启动时扫描 group "item_spawn_point" 的所有节点
##   2. 按 item_id 分组，每组就是一个生成器
##   3. 每组维持「同 item_id 同时存在不超过点位总数的 60%」
##   4. 物品被拾取后，从该组其他空闲点位中随机选一个再生
##
## 编辑器里 ItemSpawnPoint 节点显示彩色圆圈+物品名，可拖动调整位置。

const PICKUP_SCENE: PackedScene = preload("res://scenes/entities/item_pickup.tscn")
const ANCHOR_OCCUPY_RADIUS: float = 24.0  # 已生成的物品占用判定半径
const RESPAWN_INTERVAL_MIN: float = 18.0
const RESPAWN_INTERVAL_MAX: float = 45.0
const MAX_ALIVE_RATIO: float = 0.6  # 同时存在的比例（点位数 × 此比例）

var _groups: Dictionary = {}  # item_id → Array[ItemSpawnPoint]
var _alive: Dictionary  = {}  # item_id → Array[ItemPickup]
var _next_at: Dictionary = {} # item_id → next spawn unix time
var _pickups_parent: Node = null


func _ready() -> void:
	# 等场景就绪
	await get_tree().process_frame
	await get_tree().process_frame
	_pickups_parent = get_tree().get_root().get_node_or_null("Main/Pickups")
	if _pickups_parent == null:
		_pickups_parent = get_parent()

	_scan_spawn_points()
	# 启动种子（每组先放一半）
	for item_id in _groups:
		var pts: Array = _groups[item_id]
		var max_a: int = max(1, int(pts.size() * MAX_ALIVE_RATIO))
		for _i in range(max_a):
			_try_spawn(item_id)
	# 周期检查
	var t := Timer.new()
	t.wait_time = 2.0
	t.timeout.connect(_tick)
	add_child(t)
	t.start()
	print("[ItemSpawner] 启动，%d 种物品，共 %d 个生成点" % [
		_groups.size(),
		_groups.values().reduce(func(acc, arr): return acc + arr.size(), 0)
	])


func _scan_spawn_points() -> void:
	_groups.clear()
	for node in get_tree().get_nodes_in_group("item_spawn_point"):
		var iid: String = node.item_id if "item_id" in node else ""
		if iid == "": continue
		if not _groups.has(iid):
			_groups[iid] = []
			_alive[iid] = []
			_next_at[iid] = 0.0
		_groups[iid].append(node)


func _tick() -> void:
	var now: float = Time.get_ticks_msec() / 1000.0
	for item_id in _groups:
		# 清理已 free 的 pickup
		_alive[item_id] = _alive[item_id].filter(func(n): return is_instance_valid(n))
		var pts: Array = _groups[item_id]
		var max_a: int = max(1, int(pts.size() * MAX_ALIVE_RATIO))
		if _alive[item_id].size() >= max_a:
			continue
		if now < float(_next_at[item_id]):
			continue
		_try_spawn(item_id)
		_next_at[item_id] = now + randf_range(RESPAWN_INTERVAL_MIN, RESPAWN_INTERVAL_MAX)


## 在该 item_id 的某个未占用 spawn point 位置生成一个物品
func _try_spawn(item_id: String) -> void:
	var pts: Array = _groups.get(item_id, [])
	if pts.is_empty(): return

	# 找空闲点位（其位置 ANCHOR_OCCUPY_RADIUS 内无 alive pickup）
	var free_points: Array = []
	for p in pts:
		if not is_instance_valid(p): continue
		var occupied := false
		for pickup in _alive[item_id]:
			if not is_instance_valid(pickup): continue
			if pickup.global_position.distance_to(p.global_position) < ANCHOR_OCCUPY_RADIUS:
				occupied = true
				break
		if not occupied:
			free_points.append(p)
	if free_points.is_empty(): return

	var spawn_point: ItemSpawnPoint = free_points[randi() % free_points.size()]
	var pickup: ItemPickup = PICKUP_SCENE.instantiate()
	pickup.item_id = item_id
	pickup.global_position = spawn_point.global_position
	if _pickups_parent != null:
		_pickups_parent.add_child(pickup)
		_alive[item_id].append(pickup)
