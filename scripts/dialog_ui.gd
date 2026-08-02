extends CanvasLayer
## 对话框 UI（聊天模式）
##
## 用法（外部）：
##   open_chat(animal_id, speaker_name)            # 开始一段对话
##   show_npc_line(text)                            # 追加一句 NPC 发言（带打字机）
##   set_status("等待中...")                         # 设状态文字（右上角）
##   close()                                        # 关闭对话
##
## 信号：
##   chat_send_requested(animal_id, user_text)      # 玩家敲回车要发消息
##   dialog_finished(animal_id)                     # 关闭

signal chat_send_requested(animal_id: String, user_text: String)
signal gift_send_requested(animal_id: String, item_id: String)
signal dialog_finished(animal_id: String)

@export var typewriter_speed: float = 30.0       # 字符/秒
@export var max_log_lines: int = 200             # 简单防止无限增长

var _is_open: bool = false
var _is_typing: bool = false
var _typing_prefix: String = ""                  # NPC 发言名字前缀 BBCode（立刻全显）
var _typing_content: String = ""                 # 实际内容（逐字显示）
var _typing_progress: float = 0.0
var _log_buffer: String = ""                     # 累计的 BBCode 历史
var _animal_id: String = ""

@onready var panel: Panel = %Panel
@onready var name_label: Label = %SpeakerName
@onready var status_label: Label = %StatusLabel
@onready var text_label: RichTextLabel = %DialogText
@onready var input_line: LineEdit = %InputLine
@onready var gift_button: Button = %GiftButton
@onready var gossip_button: Button = %GossipButton
@onready var gossip_bar: Panel = %GossipBar
@onready var inquire_button: Button = %InquireButton
@onready var spread_button: Button = %SpreadButton
@onready var debunk_button: Button = %DebunkButton

var _last_rumor_id: int = 0   # 最近打听到的话题（供辟谣）

var _spread_mode: bool = false          # 造谣输入模式：回车发谣言而非普通聊天
var _default_placeholder: String = ""   # 输入框默认占位文字
const SPREAD_PLACEHOLDER := "谣言：想让大家传什么？回车放出…"

var _mayor_task: Dictionary = {}     # 当前进行中的镇务任务视图（空=无）
var _mayor_offered: bool = false     # 本次对话是否已给出安排选项
var _mayor_assigning: bool = false   # 已点选、等待后端结算

var _quiz_pending: bool = false      # 当前有一道未作答的 NPC 提问
var _quiz_id: String = ""
var _quiz_options: Array = []


func _ready() -> void:
	close()
	_default_placeholder = input_line.placeholder_text
	input_line.text_submitted.connect(_on_input_submitted)
	gift_button.pressed.connect(_on_gift_button_pressed)
	gossip_button.pressed.connect(_on_gossip_button_pressed)
	inquire_button.pressed.connect(_on_inquire_pressed)
	spread_button.pressed.connect(_on_spread_pressed)
	debunk_button.pressed.connect(_on_debunk_pressed)
	AgentClient.chat_intent_made.connect(_on_chat_intent_made)
	AgentClient.rumor_reply_received.connect(_on_rumor_reply)
	AgentClient.quiz_asked.connect(_on_quiz_asked)
	AgentClient.quiz_result_received.connect(_on_quiz_result)
	if AgentClient.has_signal("mayor_task_state_received"):
		AgentClient.mayor_task_state_received.connect(_on_mayor_state)
	if AgentClient.has_signal("mayor_task_result_received"):
		AgentClient.mayor_task_result_received.connect(_on_mayor_result)
	if text_label:
		text_label.meta_clicked.connect(_on_meta_clicked)
	# 背包 UI 送礼选中信号
	var inv_ui := get_tree().get_root().find_child("InventoryUI", true, false)
	if inv_ui and inv_ui.has_signal("gift_item_chosen"):
		inv_ui.gift_item_chosen.connect(_on_gift_item_chosen)



func _process(delta: float) -> void:
	if not _is_typing:
		return
	_typing_progress += typewriter_speed * delta
	var n: int = min(int(_typing_progress), _typing_content.length())
	text_label.text = _log_buffer + _typing_prefix + _typing_content.substr(0, n)
	if n >= _typing_content.length():
		# 完成，提交进 log
		_log_buffer += _typing_prefix + _typing_content + "\n\n"
		_typing_prefix = ""
		_typing_content = ""
		_is_typing = false
		text_label.text = _log_buffer
		_scroll_to_bottom()


func _unhandled_input(event: InputEvent) -> void:
	if not _is_open:
		return
	if event is InputEventKey and event.pressed:
		var key_event := event as InputEventKey
		# Esc 关闭（若在造谣模式则先取消模式）
		if key_event.keycode == KEY_ESCAPE:
			if _spread_mode:
				_exit_spread_mode()
			else:
				close()
			get_viewport().set_input_as_handled()
			return
		# 打字时任意键加速
		if _is_typing and not input_line.has_focus():
			_skip_typing()
			get_viewport().set_input_as_handled()


# ---------- 公共接口 ----------

func open_chat(animal_id: String, speaker: String) -> void:
	_animal_id = animal_id
	name_label.text = speaker
	_log_buffer = ""
	_typing_prefix = ""
	_typing_content = ""
	_is_typing = false
	text_label.text = ""
	status_label.text = ""
	input_line.text = ""
	input_line.editable = false  # 等 NPC 开口完才能发
	gift_button.disabled = true  # 等 greet 完才允许送礼
	gossip_button.disabled = true
	gossip_bar.hide()
	_last_rumor_id = 0
	_mayor_offered = false
	_mayor_assigning = false
	_quiz_pending = false
	_quiz_id = ""
	_quiz_options = []
	_spread_mode = false
	input_line.placeholder_text = _default_placeholder
	panel.show()
	_is_open = true
	_maybe_offer_mayor_task()


func show_npc_line(text: String) -> void:
	# 触发打字机效果
	_typing_prefix = "[b][color=#704020]%s：[/color][/b]" % name_label.text
	_typing_content = text
	_typing_progress = 0.0
	_is_typing = true
	status_label.text = ""
	input_line.editable = true
	gift_button.disabled = false
	gossip_button.disabled = false
	input_line.grab_focus()


func append_player_line(text: String) -> void:
	# 玩家发言直接进 log（无打字机）
	var formatted := "[b][color=#205080]你：[/color][/b]%s\n\n" % text
	_log_buffer += formatted
	if _is_typing:
		text_label.text = _log_buffer + _typing_prefix + _typing_content.substr(0, int(_typing_progress))
	else:
		text_label.text = _log_buffer
	_scroll_to_bottom()


func set_status(text: String) -> void:
	status_label.text = text


func set_input_enabled(enabled: bool) -> void:
	input_line.editable = enabled
	if enabled:
		input_line.grab_focus()


func close() -> void:
	# 关键：先释放输入框焦点，否则隐藏后仍吃键盘事件
	if input_line:
		input_line.release_focus()
		input_line.placeholder_text = _default_placeholder
	_spread_mode = false
	panel.hide()
	_is_open = false
	_is_typing = false
	var aid := _animal_id
	_animal_id = ""
	dialog_finished.emit(aid)


func is_open() -> bool:
	return _is_open


# ---------- 内部 ----------

func _on_input_submitted(text: String) -> void:
	var t := text.strip_edges()
	if t == "":
		return
	if _animal_id == "":
		return
	input_line.text = ""
	# 造谣模式：回车放出谣言（而非普通聊天）
	if _spread_mode:
		_exit_spread_mode()
		input_line.editable = false
		_append_log("[b][color=#205080]你：[/color][/b][i]（压低声音放话）[/i]%s\n\n" % t)
		status_label.text = "正在放话..."
		AgentClient.request_rumor_spread(_animal_id, t)
		return
	input_line.editable = false
	chat_send_requested.emit(_animal_id, t)


func _skip_typing() -> void:
	if not _is_typing:
		return
	_log_buffer += _typing_prefix + _typing_content + "\n\n"
	_typing_prefix = ""
	_typing_content = ""
	_is_typing = false
	text_label.text = _log_buffer
	_scroll_to_bottom()
	input_line.editable = true


func _scroll_to_bottom() -> void:
	# RichTextLabel 滚到底（如内容超过区域）
	var sb := text_label.get_v_scroll_bar()
	if sb:
		sb.value = sb.max_value


# ---------- 礼物（统一用背包 UI）----------

func _on_gift_button_pressed() -> void:
	if _animal_id == "":
		return
	# 打开背包 UI 的送礼模式
	var inv_ui := get_tree().get_root().find_child("InventoryUI", true, false)
	if inv_ui and inv_ui.has_method("open_for_gift"):
		inv_ui.open_for_gift(_animal_id)
	if input_line:
		input_line.release_focus()


func _on_gift_item_chosen(item_id: String) -> void:
	if _animal_id == "" or not _is_open:
		return
	if not PlayerInventory.has_item(item_id):
		return
	# 对话记录行
	var item_name: String = ItemDB.get_item_name(item_id)
	var formatted := "[b][color=#205080]你：[/color][/b][i]（送了一份 %s）[/i]\n\n" % item_name
	_append_log(formatted)
	# 锁输入等回应
	input_line.editable = false
	gift_button.disabled = true
	status_label.text = "正在思考..."
	gift_send_requested.emit(_animal_id, item_id)


func _on_chat_intent_made(animal_id: String, target_name: String, summary: String) -> void:
	## NPC 在对话中承诺去找某人 → 在对话框追加一行小字提示
	if animal_id != _animal_id or not _is_open:
		return
	var note := "[color=#7a5c3a][i]📅 （%s 答应了：%s）[/i][/color]\n\n" % [name_label.text, summary]
	_append_log(note)


func show_npc_gift_note(message: String) -> void:
	## NPC 升到 love，赠送签名礼物 → 对话框显示特殊提示
	if not _is_open:
		return
	var note := "[color=#c0a000][b]🎁 %s[/b][/color]\n\n" % message
	_append_log(note)


func _append_log(bbcode: String) -> void:
	_log_buffer += bbcode
	if _is_typing:
		text_label.text = _log_buffer + _typing_prefix + _typing_content.substr(0, int(_typing_progress))
	else:
		text_label.text = _log_buffer
	_scroll_to_bottom()


## 等当前这句 NPC 台词打完再追加内容。
## 打字机把台词暂存在 _typing_content，完成后才并入 _log_buffer；
## 若此时直接 _append_log，附加内容会抢先入库、显示在台词上方。
func _append_log_after_typing(bbcode: String) -> void:
	while _is_typing and _is_open:
		await get_tree().process_frame
	if not _is_open:
		return          # 期间玩家关了对话，丢弃这段追加
	_append_log(bbcode)


# ---------- 八卦（打听 / 放话 / 辟谣）----------

func _on_gossip_button_pressed() -> void:
	gossip_bar.visible = not gossip_bar.visible
	if gossip_bar.visible:
		status_label.text = "造谣时提到某人并说好话/坏话，会左右TA的选情"


func _on_inquire_pressed() -> void:
	if _animal_id == "":
		return
	gossip_bar.hide()
	_append_log("[b][color=#205080]你：[/color][/b][i]（凑近打听最近的新鲜事）[/i]\n\n")
	status_label.text = "正在打听..."
	AgentClient.request_rumor_inquire(_animal_id)


func _on_spread_pressed() -> void:
	if _animal_id == "":
		return
	gossip_bar.hide()
	# 进入造谣模式：改占位提示并聚焦，玩家回车即按谣言发出
	_spread_mode = true
	input_line.editable = true
	input_line.placeholder_text = SPREAD_PLACEHOLDER
	input_line.grab_focus()
	status_label.text = "造谣模式：输入后回车放出谣言（Esc 取消）"


func _exit_spread_mode() -> void:
	if not _spread_mode:
		return
	_spread_mode = false
	input_line.placeholder_text = _default_placeholder
	status_label.text = ""


func _on_debunk_pressed() -> void:
	if _animal_id == "":
		return
	gossip_bar.hide()
	if _last_rumor_id <= 0:
		status_label.text = "先打听到一条传闻，才能辟谣"
		return
	input_line.editable = false
	_append_log("[b][color=#205080]你：[/color][/b][i]（郑重澄清那是谣传）[/i]\n\n")
	status_label.text = "正在辟谣..."
	AgentClient.request_rumor_debunk(_animal_id, _last_rumor_id)


func _on_rumor_reply(info: Dictionary) -> void:
	if String(info.get("animal_id", "")) != _animal_id or not _is_open:
		return
	if int(info.get("rumor_id", 0)) > 0 and info.get("has_rumor", false):
		_last_rumor_id = int(info.get("rumor_id", 0))
	show_npc_line(String(info.get("text", "")))
	_append_intel(info)


## 打听回包里的情报条目：按类型上色，逐条列出
func _append_intel(info: Dictionary) -> void:
	var tips = info.get("intel", [])
	if typeof(tips) != TYPE_ARRAY or tips.is_empty():
		var hint := String(info.get("intel_hint", ""))
		if hint != "":
			_append_log_after_typing("[color=#6a6a5a][i]%s[/i][/color]\n\n" % hint)
		return
	var colors := {
		"gift": "#2a6a2a", "dislike": "#a03020",
		"attitude": "#2a5a8a", "vote": "#7a3a18", "rumor": "#5a4a2a",
		"tie_good": "#1f6b5a", "tie_bad": "#8a2f6a",
	}
	var line := "[color=#4a3a28][b]📋 打听到的消息[/b][/color]\n"
	for t in tips:
		if typeof(t) != TYPE_DICTIONARY:
			continue
		var kind := String(t.get("kind", ""))
		var col: String = colors.get(kind, "#4a3a28")
		line += "  %s [color=%s]%s[/color]\n" % [
			String(t.get("icon", "·")), col, String(t.get("text", "")),
		]
	var hint2 := String(info.get("intel_hint", ""))
	if hint2 != "":
		line += "[color=#6a6a5a][i]%s[/i][/color]\n" % hint2
	_append_log_after_typing(line + "\n")


# ---------- NPC 问答（考一考玩家是否了解自己）----------

func _on_quiz_asked(animal_id: String, quiz_id: String, question: String, options: Array) -> void:
	if animal_id != _animal_id or not _is_open or _quiz_pending:
		return
	_quiz_pending = true
	_quiz_id = quiz_id
	_quiz_options = options
	show_npc_line(question)
	var line := "[color=#4a3a28][b]❓ 你怎么答？[/b][/color]\n"
	for i in options.size():
		line += "  [url=quiz:%d][color=#2a5a8a]【%s】[/color][/url]\n" % [i, String(options[i])]
	_append_log_after_typing(line + "\n")


func _on_quiz_result(info: Dictionary) -> void:
	if String(info.get("animal_id", "")) != _animal_id or not _is_open:
		return
	_quiz_pending = false
	_quiz_id = ""
	_quiz_options = []
	input_line.editable = true
	gift_button.disabled = false
	gossip_button.disabled = false
	status_label.text = ""
	if info.get("already", false):
		await _append_log_after_typing("[color=#6a6a5a][i]这题已经答过了。[/i][/color]\n\n")
		return
	var delta := int(info.get("delta", 0))
	if info.get("correct", false):
		await _append_log_after_typing("[color=#2a6a2a][b]✅ 答对了！[/b]好感 +%d[/color]\n\n" % delta)
	else:
		await _append_log_after_typing("[color=#a03020][b]❌ 答错了。[/b]正确答案：%s（好感 %d）[/color]\n\n" % [
			String(info.get("answer", "")), delta,
		])
	show_npc_line(String(info.get("text", "")))


# ---------- 镇务任务（现任镇长：安排 NPC 干活）----------

func _on_mayor_state(info: Dictionary) -> void:
	if info.get("active", false):
		_mayor_task = info.get("task", {})
	else:
		_mayor_task = {}


func _can_assign_here() -> bool:
	if _mayor_task.is_empty() or _animal_id == "" or _animal_id == "player":
		return false
	# 目标本人（酒鬼/病人）不能被派去处置自己
	var tgt := String(_mayor_task.get("target_id", ""))
	if tgt != "" and _animal_id == tgt:
		return false
	return true


func _maybe_offer_mayor_task() -> void:
	if not _can_assign_here():
		return
	_mayor_offered = true
	var title := String(_mayor_task.get("title", "镇务"))
	var tgt := String(_mayor_task.get("target_name", ""))
	var obj := ("（对象：%s）" % tgt) if tgt != "" else ""
	var line := "[color=#7a3a18][b]🏛【镇务】[/b]以镇长身份安排 %s 去「%s」%s[/color]\n" % [name_label.text, title, obj]
	line += "[color=#4a3a28]方式：[/color]"
	line += "[url=mayor:persuade][color=#2a6a2a]【好感说服】[/color][/url]  "
	line += "[url=mayor:command][color=#a03020]【命令】（耗1权力点）[/color][/url]\n\n"
	_append_log(line)


func _on_meta_clicked(meta) -> void:
	var s := String(meta)
	if s.begins_with("quiz:"):
		if not _quiz_pending:
			return
		var idx := int(s.substr(5))
		if idx < 0 or idx >= _quiz_options.size():
			return
		var choice := String(_quiz_options[idx])
		_quiz_pending = false
		_append_log("[b][color=#205080]你：[/color][/b]%s\n\n" % choice)
		input_line.editable = false
		gift_button.disabled = true
		gossip_button.disabled = true
		status_label.text = "等待回应..."
		AgentClient.send_quiz_answer(_animal_id, _quiz_id, choice)
		return
	if not s.begins_with("mayor:"):
		return
	if not _mayor_offered or _mayor_assigning or _mayor_task.is_empty():
		return
	var method := s.substr(6)
	if method not in ["persuade", "command"]:
		return
	_mayor_assigning = true
	_mayor_offered = false
	var method_cn: String = {"persuade": "好言相劝", "command": "动用镇长权力下令"}.get(method, method)
	_append_log("[b][color=#205080]你：[/color][/b][i]（以镇长身份%s，安排 TA 去办这事）[/i]\n\n" % method_cn)
	input_line.editable = false
	gift_button.disabled = true
	gossip_button.disabled = true
	status_label.text = "正在安排..."
	AgentClient.request_mayor_task_assign(int(_mayor_task.get("id", 0)), _animal_id, method)


func _on_mayor_result(info: Dictionary) -> void:
	if String(info.get("executor_id", "")) != _animal_id or not _is_open:
		return
	# 后端拒绝（如：此人无法执行该任务）→ 提示错误，重新开放选项
	if not info.get("ok", true):
		_mayor_assigning = false
		_mayor_offered = true
		status_label.text = String(info.get("error", "无法安排此人"))
		input_line.editable = true
		gift_button.disabled = false
		gossip_button.disabled = false
		return
	if not info.get("accepted", false):
		# 拒绝：仍在对话内，重新开放选项，允许玩家换方式再试
		_mayor_assigning = false
		_mayor_offered = true
		show_npc_line(String(info.get("line", "这活儿我可不干。")))
		return
	# 接受：显示答应台词，稍候关闭对话，交由 main 演出前往目的地
	_mayor_task = {}
	show_npc_line(String(info.get("accept_line", "行，交给我。")))
	_close_after_accept()


func _close_after_accept() -> void:
	await get_tree().create_timer(1.6).timeout
	if _is_open:
		close()
