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
@onready var gift_picker: Panel = %GiftPicker
@onready var gift_grid: GridContainer = %GiftGrid
@onready var gift_empty: Label = %GiftEmpty
@onready var gift_close: Button = %GiftClose


func _ready() -> void:
	close()
	input_line.text_submitted.connect(_on_input_submitted)
	gift_button.pressed.connect(_on_gift_button_pressed)
	gift_close.pressed.connect(_on_gift_close_pressed)


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
		# Esc 关闭（若 picker 打开则先关 picker）
		if key_event.keycode == KEY_ESCAPE:
			if gift_picker.visible:
				_on_gift_close_pressed()
			else:
				close()
			get_viewport().set_input_as_handled()
			return
		# 打字时任意键加速（除非焦点在 LineEdit 中或 picker 显示中）
		if _is_typing and not input_line.has_focus() and not gift_picker.visible:
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
	gift_picker.visible = false
	gift_button.disabled = true  # 等 greet 完才允许送礼
	panel.show()
	_is_open = true


func show_npc_line(text: String) -> void:
	# 触发打字机效果
	_typing_prefix = "[b][color=#704020]%s：[/color][/b]" % name_label.text
	_typing_content = text
	_typing_progress = 0.0
	_is_typing = true
	status_label.text = ""
	input_line.editable = true
	gift_button.disabled = false
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


# ---------- 礼物面板 ----------

func _on_gift_button_pressed() -> void:
	if _animal_id == "":
		return
	_rebuild_gift_grid()
	gift_picker.visible = true
	# 失焦输入框，避免 Esc 走不到我们手里
	if input_line:
		input_line.release_focus()


func _on_gift_close_pressed() -> void:
	gift_picker.visible = false
	input_line.grab_focus()


func _rebuild_gift_grid() -> void:
	# 清空现有按钮
	for c in gift_grid.get_children():
		c.queue_free()
	var inv: Dictionary = PlayerInventory.get_all()
	if inv.is_empty():
		gift_empty.visible = true
		return
	gift_empty.visible = false
	for item_id in inv.keys():
		var count: int = int(inv[item_id])
		var btn := _make_gift_button(item_id, count)
		gift_grid.add_child(btn)


func _make_gift_button(item_id: String, count: int) -> Button:
	var def: Dictionary = ItemDB.get_def(item_id)
	var item_name: String = def.get("name", item_id)
	var base_value: int = int(def.get("base_value", 0))
	var btn := Button.new()
	btn.custom_minimum_size = Vector2(110, 56)
	btn.tooltip_text = "%s\n%s\n基础价值 +%d" % [item_name, def.get("desc", ""), base_value]
	var btn_icon: Texture2D = ItemDB.get_icon(item_id)
	btn.icon = btn_icon
	btn.expand_icon = false
	btn.icon_alignment = HORIZONTAL_ALIGNMENT_LEFT
	btn.text = " %s ×%d" % [item_name, count]
	btn.alignment = HORIZONTAL_ALIGNMENT_LEFT
	btn.add_theme_font_size_override("font_size", 14)
	btn.pressed.connect(_on_gift_item_chosen.bind(item_id))
	return btn


func _on_gift_item_chosen(item_id: String) -> void:
	if _animal_id == "":
		return
	if not PlayerInventory.has_item(item_id):
		return
	# 关闭面板（避免连点）
	gift_picker.visible = false
	# 玩家发言行（"（送了一份 X）"）
	var item_name: String = ItemDB.get_item_name(item_id)
	var formatted := "[b][color=#205080]你：[/color][/b][i]（送了一份 %s）[/i]\n\n" % item_name
	_log_buffer += formatted
	if _is_typing:
		text_label.text = _log_buffer + _typing_prefix + _typing_content.substr(0, int(_typing_progress))
	else:
		text_label.text = _log_buffer
	_scroll_to_bottom()
	# 锁输入等回应
	input_line.editable = false
	gift_button.disabled = true
	status_label.text = "正在思考..."
	# 派发请求（main.gd 接信号 → 扣库存 + 发 ws）
	gift_send_requested.emit(_animal_id, item_id)
