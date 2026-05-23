extends Node2D
## 主场景控制器
##
## 流程：
##   玩家 E → 打开对话框 → 请求后端 greet → NPC 开口
##   玩家输入回车 → 请求后端 chat → NPC 回应
##   后端断开 → 显示提示，仍可关闭对话

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
	dialog_ui.dialog_finished.connect(_on_dialog_finished)

	# 后端信号
	AgentClient.reply_received.connect(_on_reply_received)
	AgentClient.error_received.connect(_on_error_received)


# ---------- 玩家交互 ----------

func _on_player_interact(target: Node) -> void:
	if dialog_ui.is_open():
		return
	if not (target is Animal):
		return
	var animal: Animal = target
	_current_animal = animal

	# 锁玩家输入，避免打字时角色乱跑
	player.input_enabled = false

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


func _on_dialog_finished(_animal_id: String) -> void:
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


# ---------- 上下文构造 ----------

func _build_context(animal: Animal) -> Dictionary:
	var loc_id: String = animal.get_target_location()
	return {
		"time": WorldClock.format_time(),
		"location": loc_id,
		"location_label": LocationDB.get_label(loc_id),
		"intent": animal.get_current_intent(),
	}
