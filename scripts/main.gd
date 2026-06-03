extends Node2D
## 主场景控制器
##
## 流程：
##   玩家 E → 打开对话框 → 请求后端 greet → NPC 开口
##   玩家输入回车 → 请求后端 chat → NPC 回应
##   后端断开 → 显示提示，仍可关闭对话
##   玩家走远（> auto_close_distance）→ 自动关闭对话

@export var auto_close_distance: float = 130.0

@onready var player: CharacterBody2D = %Player
@onready var dialog_ui: CanvasLayer = %DialogUI

var _current_animal: Animal = null


func _ready() -> void:
	if player == null:
		push_error("Main: 找不到 Player 节点")
		return
	player.interact_pressed.connect(_on_player_interact)

	# 对话框信号
	dialog_ui.chat_send_requested.connect(_on_chat_send)
	dialog_ui.gift_send_requested.connect(_on_gift_send)
	dialog_ui.dialog_finished.connect(_on_dialog_finished)

	# 后端信号
	AgentClient.reply_received.connect(_on_reply_received)
	AgentClient.affection_changed.connect(_on_affection_changed)
	AgentClient.error_received.connect(_on_error_received)
	AgentClient.npc_intent_received.connect(_on_npc_intent)
	AgentClient.npc_gift_received.connect(_on_npc_gift)
	AgentClient.quest_offer_received.connect(_on_quest_offer)
	AgentClient.quest_completed_received.connect(_on_quest_completed)


func _process(_delta: float) -> void:
	# 对话期间，玩家走远 → 自动关闭
	if _current_animal == null or not dialog_ui.is_open():
		return
	if not is_instance_valid(_current_animal):
		dialog_ui.close()
		return
	var d: float = player.global_position.distance_to(_current_animal.global_position)
	if d > auto_close_distance:
		dialog_ui.close()


# ---------- 玩家交互 ----------

func _on_player_interact(target: Node) -> void:
	if dialog_ui.is_open():
		return
	# 拾取物品
	if target.is_in_group("pickup") and target.has_method("pickup"):
		target.pickup()
		return
	if not (target is Animal):
		return
	var animal: Animal = target
	# 对方正在和别人交谈：拒绝开始对话
	if animal.is_busy():
		return
	_current_animal = animal

	# 锁玩家输入，避免打字时角色乱跑
	player.input_enabled = false

	# NPC 进入"和玩家对话"状态：停步 + 朝向玩家
	animal.set_busy(animal.BusyState.TALKING_PLAYER)
	animal.face_to(player.global_position)

	dialog_ui.open_chat(animal.animal_id, animal.animal_name)

	if not AgentClient.is_connected_to_server():
		dialog_ui.set_status("（未连后端）")
		dialog_ui.show_npc_line("……（这个动物似乎没有灵魂。请先启动 agent_server）")
		return

	dialog_ui.set_status("正在思考...")
	_track_talked_to(animal.animal_id)
	# 玩家也访问到了 NPC 当前所在地点
	_track_visit(animal.get_target_location())
	AgentClient.request_greet(animal.animal_id, _build_context(animal))


func _on_chat_send(animal_id: String, user_text: String) -> void:
	if _current_animal == null or _current_animal.animal_id != animal_id:
		return
	dialog_ui.append_player_line(user_text)

	if not AgentClient.is_connected_to_server():
		dialog_ui.show_npc_line("……（连不上服务器）")
		return

	dialog_ui.set_status("正在思考...")
	AgentClient.request_chat(animal_id, user_text, _build_context(_current_animal))


func _on_gift_send(animal_id: String, item_id: String) -> void:
	if _current_animal == null or _current_animal.animal_id != animal_id:
		return
	if not PlayerInventory.has_item(item_id):
		dialog_ui.set_status("（你没有这个物品）")
		return
	if not AgentClient.is_connected_to_server():
		dialog_ui.show_npc_line("……（连不上服务器，礼物没送出去）")
		return
	# 客户端先扣库存（即使服务端失败也无伤大雅，物品散落即可补给）
	PlayerInventory.remove_item(item_id, 1)
	AgentClient.request_gift(animal_id, item_id, _build_context(_current_animal))


func _on_dialog_finished(_animal_id: String) -> void:
	# NPC 解除 busy，恢复日程
	if _current_animal and is_instance_valid(_current_animal):
		_current_animal.clear_busy()
	_current_animal = null
	# 解锁玩家输入
	player.input_enabled = true


# ---------- 后端回复 ----------

func _on_reply_received(animal_id: String, text: String) -> void:
	if not dialog_ui.is_open():
		return
	if _current_animal == null or _current_animal.animal_id != animal_id:
		return
	dialog_ui.show_npc_line(text)


func _on_error_received(message: String) -> void:
	if not dialog_ui.is_open():
		return
	dialog_ui.set_status("出错：%s" % message)
	dialog_ui.set_input_enabled(true)


func _on_npc_gift(animal_id: String, item_id: String, _item_name: String, message: String) -> void:
	## NPC 升到 love 时赠送签名礼物，加入背包并在对话框显示
	PlayerInventory.add_item(item_id, 1)
	if dialog_ui.is_open():
		dialog_ui.show_npc_gift_note(message)


func _on_quest_offer(_aid: String, _qid: String, title: String, desc: String) -> void:
	if dialog_ui.is_open():
		dialog_ui.show_npc_gift_note("📜 新任务\n%s\n%s" % [title, desc])


func _on_quest_completed(animal_id: String, _qid: String, title: String, reward_item: String, reward_count: int) -> void:
	if reward_item != "" and reward_count > 0:
		PlayerInventory.add_item(reward_item, reward_count)
	if dialog_ui.is_open():
		var item_name := ItemDB.get_item_name(reward_item) if reward_item else ""
		var msg := "✅ 任务完成：%s" % title
		if item_name:
			msg += "\n获得 %s × %d" % [item_name, reward_count]
		dialog_ui.show_npc_gift_note(msg)
	# 让该 NPC 头顶冒星星
	for n in get_tree().get_nodes_in_group("npc"):
		if n is Animal and n.animal_id == animal_id and n.has_method("show_emote"):
			n.show_emote("🎉", 2.0, 0.0)
			break


func _on_affection_changed(animal_id: String, value: int, level: String, delta: int) -> void:
	# 找到对应 animal 节点把好感度状态推过去（emote + 飘字）。
	# 不强制依赖 _current_animal，遍历群组兼容多种触发场景（如未来世界事件）。
	for n in get_tree().get_nodes_in_group("npc"):
		if n is Animal and n.animal_id == animal_id:
			n.update_affection(value, level, delta)
			break


func _on_npc_intent(initiator_id: String, target_id: String, _intent_text: String) -> void:
	## 后端推送 NPC 自发意图：initiator 主动走向 target，到达后发起对话
	if not AgentClient.is_connected_to_server():
		return
	if target_id == "":
		return
	var initiator: Animal = _find_animal(initiator_id)
	var target: Animal = _find_animal(target_id)
	if initiator == null or target == null:
		return
	if initiator.is_busy() or target.is_busy():
		return
	# target 原地等待，面向 initiator
	target.face_to(initiator.global_position)
	# initiator 主动走过去，到达后触发对话
	initiator.approach_for_intent(
		target.global_position,
		func():
			# 到达后双方面对面，置 busy，发 npc_chat
			initiator.face_to(target.global_position)
			target.face_to(initiator.global_position)
			initiator.set_busy(Animal.BusyState.TALKING_NPC, 14.0)
			target.set_busy(Animal.BusyState.TALKING_NPC, 14.0)
			AgentClient.request_npc_chat(
				initiator_id, target_id, initiator.get_current_context()
			)
	)


func _find_animal(animal_id: String) -> Animal:
	for n in get_tree().get_nodes_in_group("npc"):
		if n is Animal and n.animal_id == animal_id:
			return n
	return null


# ---------- 上下文构造 ----------

## 任务追踪：玩家访问过的地点 + 聊过的 NPC（最多保留最近 8 项）
var _visited_locations: Array[String] = []
var _talked_to_npcs:    Array[String] = []


func _build_context(animal: Animal) -> Dictionary:
	var loc_id: String = animal.get_target_location()
	# 附近其他 NPC 名字（用于"附近还有谁"prompt 注入）
	var nearby: Array = []
	for n in get_tree().get_nodes_in_group("npc"):
		if n == animal: continue
		if not is_instance_valid(n): continue
		if n.global_position.distance_to(animal.global_position) > 120.0: continue
		var nm: String = n.animal_name if "animal_name" in n else ""
		if nm != "": nearby.append(nm)
	# 玩家背包（任务系统用）
	var inv: Dictionary = PlayerInventory.get_all() if has_node("/root/PlayerInventory") else {}
	return {
		"time": WorldClock.format_time(),
		"game_day": WorldClock.get_day(),
		"location": loc_id,
		"location_label": LocationDB.get_label(loc_id),
		"intent": animal.get_current_intent(),
		"nearby_npcs": nearby,
		"inventory": inv,
		"visited_locations": _visited_locations.duplicate(),
		"talked_to_npcs": _talked_to_npcs.duplicate(),
	}


## 玩家访问/聊天历史追踪（任务完成判定用）
func _track_visit(loc_id: String) -> void:
	if loc_id == "" or loc_id in _visited_locations:
		return
	_visited_locations.append(loc_id)
	if _visited_locations.size() > 12:
		_visited_locations.pop_front()


func _track_talked_to(animal_id: String) -> void:
	if animal_id == "":
		return
	# 移到最后（最近）
	_talked_to_npcs.erase(animal_id)
	_talked_to_npcs.append(animal_id)
	if _talked_to_npcs.size() > 12:
		_talked_to_npcs.pop_front()
