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


func _on_affection_changed(animal_id: String, value: int, level: String, delta: int) -> void:
	# 找到对应 animal 节点把好感度状态推过去（emote + 飘字）。
	# 不强制依赖 _current_animal，遍历群组兼容多种触发场景（如未来世界事件）。
	for n in get_tree().get_nodes_in_group("npc"):
		if n is Animal and n.animal_id == animal_id:
			n.update_affection(value, level, delta)
			break


# ---------- 上下文构造 ----------

func _build_context(animal: Animal) -> Dictionary:
	var loc_id: String = animal.get_target_location()
	return {
		"time": WorldClock.format_time(),
		"game_day": WorldClock.get_day(),
		"location": loc_id,
		"location_label": LocationDB.get_label(loc_id),
		"intent": animal.get_current_intent(),
	}
