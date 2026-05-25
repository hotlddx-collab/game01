extends Node
## NPC↔NPC 对话协调器（autoload）。
##
## 职责：
##   - 收集所有 NPC（"animal" 组）位置
##   - 检测共享 location 且 idle 的 NPC 对
##   - 限流（全局 + 每对冷却）
##   - 调 AgentClient 触发对话
##   - 将服务器返回的 line 派发给 speaker，让 ta 弹气泡

## 全局触发间隔（秒）：每隔多久评估一次"该不该聊"
@export var tick_interval: float = 2.0
## 单对 NPC 冷却（秒）：相同两 NPC 多久内不再聊
@export var pair_cooldown: float = 25.0
## 全局冷却（秒）：避免同一时间多对 NPC 同时聊
@export var global_cooldown: float = 4.0
## 触发条件：两 NPC 距离小于此值
@export var trigger_distance: float = 100.0
## 触发条件：两 NPC 站定（速度 <= ）。放宽到 30 = 慢走也能聊
@export var idle_speed_threshold: float = 30.0
## 调试日志
@export var verbose: bool = true

var _last_pair_time: Dictionary = {}  # "a|b" → unix_ts
var _last_global_time: float = 0.0
var _tick_accum: float = 0.0


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	if has_node("/root/AgentClient"):
		AgentClient.npc_chat_received.connect(_on_npc_chat_received)


func _process(delta: float) -> void:
	_tick_accum += delta
	if _tick_accum < tick_interval:
		return
	_tick_accum = 0.0
	_evaluate_and_trigger()


func _evaluate_and_trigger() -> void:
	# 全局冷却
	var now := Time.get_ticks_msec() / 1000.0
	if now - _last_global_time < global_cooldown:
		return
	if not AgentClient.is_connected_to_server():
		return

	# 收集所有 NPC（在 "npc" 组的 Animal 节点）
	var animals: Array = get_tree().get_nodes_in_group("npc")
	if animals.size() < 2:
		return

	# 配对检查
	for i in range(animals.size()):
		for j in range(i + 1, animals.size()):
			var a = animals[i]
			var b = animals[j]
			if not _is_pair_eligible(a, b, now):
				continue
			# 触发：随机选一方作 speaker
			var speaker: Node = a if randf() < 0.5 else b
			var listener: Node = b if speaker == a else a
			_trigger(speaker, listener)
			_last_global_time = now
			return  # 一次只触发一对


func _is_pair_eligible(a: Node, b: Node, now: float) -> bool:
	if a == null or b == null: return false
	if not a.has_method("get_animal_id") or not b.has_method("get_animal_id"):
		return false
	var aid: String = a.get_animal_id()
	var bid: String = b.get_animal_id()
	if aid == "" or bid == "": return false

	# 距离
	if (a.global_position - b.global_position).length() > trigger_distance:
		return false

	# 静止（速度低）
	if "velocity" in a and a.velocity.length() > idle_speed_threshold:
		return false
	if "velocity" in b and b.velocity.length() > idle_speed_threshold:
		return false

	# pair 冷却
	var pair_key := _pair_key(aid, bid)
	var last := float(_last_pair_time.get(pair_key, 0.0))
	if now - last < pair_cooldown:
		return false

	return true


func _pair_key(a_id: String, b_id: String) -> String:
	if a_id < b_id:
		return a_id + "|" + b_id
	return b_id + "|" + a_id


func _trigger(speaker: Node, listener: Node) -> void:
	var sid: String = speaker.get_animal_id()
	var lid: String = listener.get_animal_id()
	if verbose:
		print("[ChatManager] 触发 %s → %s" % [sid, lid])

	# 标记冷却
	_last_pair_time[_pair_key(sid, lid)] = Time.get_ticks_msec() / 1000.0

	# 构造 context（用 speaker 当前情境）
	var ctx := {}
	if speaker.has_method("get_current_context"):
		ctx = speaker.get_current_context()

	AgentClient.request_npc_chat(sid, lid, ctx)


## 服务器返回 NPC 对白
func _on_npc_chat_received(speaker_id: String, _listener_id: String, text: String) -> void:
	if verbose:
		print("[ChatManager] %s: %s" % [speaker_id, text])
	# 找到 speaker 让 ta 弹气泡
	var animals: Array = get_tree().get_nodes_in_group("npc")
	for a in animals:
		if a.has_method("get_animal_id") and a.get_animal_id() == speaker_id:
			if a.has_method("show_speech_bubble"):
				a.show_speech_bubble(text)
			break
