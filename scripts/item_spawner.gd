extends Node
## 物品生成器 - Anchor 模式
##
## 每个 spawner 有一组离散位置，从未被占用的点中随机选一个生成。
## 拾取后该位置释放，下次 respawn 时可能再被选中。
##
## 配置: data/world/spawners.json
## 用法: 在 main.tscn 加 ItemSpawner 节点，挂此脚本

const SPAWNERS_FILE: String = "res://data/world/spawners.json"
const PICKUP_SCENE: PackedScene = preload("res://scenes/entities/item_pickup.tscn")
const ANCHOR_OCCUPY_RADIUS: float = 24.0  # 该 anchor 多近内有 pickup 视为已占用

var _spawners: Array = []           # 配置数组（直接来自 JSON）
var _alive: Array = []              # _alive[i] = Array[ItemPickup]
var _next_spawn_at: Array = []      # _next_spawn_at[i] = unix 秒时间戳
var _pickups_parent: Node = null


func _ready() -> void:
	_load_config()
	# 等场景就绪
	await get_tree().process_frame
	await get_tree().process_frame
	_pickups_parent = get_tree().get_root().get_node_or_null("Main/Pickups")
	if _pickups_parent == null:
		_pickups_parent = get_parent()
	# 启动种子（每个 spawner 立即生 max_alive 个）
	for i in range(_spawners.size()):
		var sp = _spawners[i]
		var max_a: int = int(sp.get("max_alive", 1))
		for _j in range(max_a):
			_try_spawn(i)
	# 周期检查
	var t := Timer.new()
	t.wait_time = 2.5
	t.timeout.connect(_tick)
	add_child(t)
	t.start()
	print("[ItemSpawner] 启动，%d 个生成器" % _spawners.size())


func _load_config() -> void:
	if not FileAccess.file_exists(SPAWNERS_FILE):
		push_warning("ItemSpawner: 找不到 " + SPAWNERS_FILE)
		return
	var f := FileAccess.open(SPAWNERS_FILE, FileAccess.READ)
	if f == null: return
	var data = JSON.parse_string(f.get_as_text())
	f.close()
	if data == null or not data.has("spawners"):
		return
	_spawners = data["spawners"]
	for _i in range(_spawners.size()):
		_alive.append([])
		_next_spawn_at.append(0.0)


func _tick() -> void:
	var now: float = Time.get_ticks_msec() / 1000.0
	for i in range(_spawners.size()):
		# 清理已 free 的 pickup
		_alive[i] = _alive[i].filter(func(n): return is_instance_valid(n))
		var sp = _spawners[i]
		var max_a: int = int(sp.get("max_alive", 1))
		if _alive[i].size() >= max_a:
			continue
		if now < float(_next_spawn_at[i]):
			continue
		_try_spawn(i)
		_next_spawn_at[i] = now + float(sp.get("respawn_after_seconds", 30.0))


## 在该 spawner 找一个未被占用的 anchor 生成一个 pickup
func _try_spawn(i: int) -> void:
	var sp = _spawners[i]
	var anchors: Array = sp.get("anchors", [])
	if anchors.is_empty(): return

	# 找未被占用的 anchor（当前 alive pickup 距离 < ANCHOR_OCCUPY_RADIUS 视为占用）
	var occupied_indices: Dictionary = {}
	for pickup in _alive[i]:
		if not is_instance_valid(pickup): continue
		for ai in range(anchors.size()):
			var a: Array = anchors[ai]
			if a.size() < 2: continue
			var pos := Vector2(float(a[0]), float(a[1]))
			if pickup.global_position.distance_to(pos) < ANCHOR_OCCUPY_RADIUS:
				occupied_indices[ai] = true
				break
	# 从未占用的 anchor 中随机选一个
	var free_idx: Array = []
	for ai in range(anchors.size()):
		if not occupied_indices.has(ai):
			free_idx.append(ai)
	if free_idx.is_empty(): return
	var pick_ai: int = free_idx[randi() % free_idx.size()]
	var anchor: Array = anchors[pick_ai]
	if anchor.size() < 2: return

	var pickup: ItemPickup = PICKUP_SCENE.instantiate()
	pickup.item_id = String(sp.get("item_id", "flower"))
	pickup.position = Vector2(float(anchor[0]), float(anchor[1]))
	if _pickups_parent != null:
		_pickups_parent.add_child(pickup)
		_alive[i].append(pickup)
