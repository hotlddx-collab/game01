extends CanvasLayer
## 承诺面板（按 P 键打开 / 关闭）
##
## 接收 AgentClient.promise_state_received 信号刷新内容。
## 每次打开都主动 request_promise_query() 拉最新。

@onready var backdrop: ColorRect = $Backdrop
@onready var title: RichTextLabel = %Title
@onready var help_label: RichTextLabel = %HelpLabel
@onready var list_vbox: VBoxContainer = %ListVBox
@onready var empty_label: RichTextLabel = %EmptyLabel

var _last_state: Dictionary = {}


func _ready() -> void:
	backdrop.visible = false
	if AgentClient.has_signal("promise_state_received"):
		AgentClient.promise_state_received.connect(_on_state)


func _input(event: InputEvent) -> void:
	if _typing_in_textbox():
		return
	if event is InputEventKey and event.pressed and not event.echo:
		if event.keycode == KEY_P and not event.ctrl_pressed:
			_toggle()
			get_viewport().set_input_as_handled()
		elif event.keycode == KEY_ESCAPE and backdrop.visible:
			_close()
			get_viewport().set_input_as_handled()


func _toggle() -> void:
	if backdrop.visible:
		_close()
	else:
		_open()


func _open() -> void:
	backdrop.visible = true
	if AgentClient.has_method("request_promise_query"):
		AgentClient.request_promise_query()
	# 已有数据先渲染一次
	if not _last_state.is_empty():
		_render(_last_state)


func _close() -> void:
	backdrop.visible = false


func _on_state(info: Dictionary) -> void:
	_last_state = info
	if backdrop.visible:
		_render(info)


func _render(info: Dictionary) -> void:
	var active_count := int(info.get("active_count", 0))
	var max_count := int(info.get("max_count", 5))
	title.text = "[center][b]📜 承诺池 %d/%d[/b][/center]" % [active_count, max_count]

	var active: Array = info.get("active", [])
	var history: Array = info.get("history", [])

	# 清空旧条目
	for c in list_vbox.get_children():
		c.queue_free()

	if active.is_empty() and history.is_empty():
		empty_label.visible = true
	else:
		empty_label.visible = false

	# 先渲染 pending（醒目）
	for p in active:
		_add_promise_row(p, "pending")
	# 再渲染本任期已结算
	for p in history:
		var st := String(p.get("status", ""))
		if st != "pending":
			_add_promise_row(p, st)


func _add_promise_row(p: Dictionary, status: String) -> void:
	var box := PanelContainer.new()
	var sb := StyleBoxFlat.new()
	match status:
		"fulfilled":
			sb.bg_color = Color(0.10, 0.20, 0.12, 0.85)
			sb.border_color = Color(0.4, 0.7, 0.4, 0.6)
		"broken":
			sb.bg_color = Color(0.22, 0.10, 0.12, 0.85)
			sb.border_color = Color(0.7, 0.4, 0.4, 0.6)
		_:
			sb.bg_color = Color(0.16, 0.16, 0.22, 0.85)
			sb.border_color = Color(0.5, 0.55, 0.7, 0.5)
	sb.border_width_left = 2
	sb.border_width_top = 2
	sb.border_width_right = 2
	sb.border_width_bottom = 2
	sb.corner_radius_top_left = 8
	sb.corner_radius_top_right = 8
	sb.corner_radius_bottom_right = 8
	sb.corner_radius_bottom_left = 8
	box.add_theme_stylebox_override("panel", sb)

	var pad := MarginContainer.new()
	pad.add_theme_constant_override("margin_left", 12)
	pad.add_theme_constant_override("margin_top", 8)
	pad.add_theme_constant_override("margin_right", 12)
	pad.add_theme_constant_override("margin_bottom", 8)
	box.add_child(pad)

	var vb := VBoxContainer.new()
	vb.add_theme_constant_override("separation", 4)
	pad.add_child(vb)

	var npc_id := String(p.get("npc_id", ""))
	var npc_name := _id_to_name(npc_id)
	var qtitle := String(p.get("quest_title", String(p.get("quest_id", "?"))))
	var qdesc := String(p.get("quest_desc", ""))
	var kind := String(p.get("quest_kind", ""))
	var deadline := int(p.get("deadline_day", -1))
	var icon := _icon_for(status)
	var status_color := _color_for(status)
	var status_text := _label_for(status)

	var line1 := RichTextLabel.new()
	line1.bbcode_enabled = true
	line1.fit_content = true
	line1.scroll_active = false
	line1.custom_minimum_size = Vector2(0, 22)
	line1.text = "%s [b]%s[/b] · 给 [color=#cdd]%s[/color] · [color=%s]%s[/color]" % [
		icon, qtitle, npc_name, status_color, status_text
	]
	vb.add_child(line1)

	if qdesc != "":
		var line2 := RichTextLabel.new()
		line2.bbcode_enabled = true
		line2.fit_content = true
		line2.scroll_active = false
		line2.custom_minimum_size = Vector2(0, 22)
		line2.text = "[color=#bbb]%s[/color]" % qdesc
		vb.add_child(line2)

	# 行动提示（pending 时给玩家清楚说明要干啥）
	if status == "pending":
		var hint := _action_hint(kind, p, deadline)
		if hint != "":
			var line3 := RichTextLabel.new()
			line3.bbcode_enabled = true
			line3.fit_content = true
			line3.scroll_active = false
			line3.custom_minimum_size = Vector2(0, 22)
			line3.text = hint
			vb.add_child(line3)

	list_vbox.add_child(box)


func _icon_for(status: String) -> String:
	match status:
		"fulfilled": return "✅"
		"broken": return "❌"
		_: return "📝"


func _color_for(status: String) -> String:
	match status:
		"fulfilled": return "#88ee88"
		"broken": return "#ee8888"
		_: return "#ddd"


func _label_for(status: String) -> String:
	match status:
		"fulfilled": return "已兑现"
		"broken": return "已破诺"
		_: return "待兑现"


func _action_hint(kind: String, p: Dictionary, deadline: int) -> String:
	var req: Dictionary = p.get("quest_requires", {})
	var deadline_text := ""
	if deadline > 0:
		deadline_text = "  [color=#888](本任期 D%d 前)[/color]" % (deadline - int(p.get("accept_day", 0)) + 1)
	match kind:
		"collect":
			var iid := String(req.get("item_id", ""))
			var nm := iid
			if Engine.has_singleton("ItemDB") or get_node_or_null("/root/ItemDB"):
				nm = ItemDB.get_item_name(iid) if iid != "" else iid
			var cnt := int(req.get("count", 1))
			return "[color=#7a9c5a]→ 收集 [b]%s × %d[/b]，再去找委托人。%s[/color]" % [nm, cnt, deadline_text]
		"deliver":
			var iid := String(req.get("item_id", ""))
			var nm := iid
			if get_node_or_null("/root/ItemDB"):
				nm = ItemDB.get_item_name(iid) if iid != "" else iid
			var tgt := String(req.get("target_npc", ""))
			return "[color=#7a9c5a]→ 把 [b]%s[/b] 送到 [b]%s[/b]。%s[/color]" % [nm, _id_to_name(tgt), deadline_text]
		"relay":
			var tgt := String(req.get("target_npc", ""))
			var msg := String(req.get("message_summary", ""))
			return "[color=#7a9c5a]→ 找 [b]%s[/b] 转告：%s%s[/color]" % [_id_to_name(tgt), msg, deadline_text]
		_:
			return "[color=#888]→ 完成对应任务即可兑现。%s[/color]" % deadline_text


func _id_to_name(npc_id: String) -> String:
	if npc_id == "" or npc_id == "player":
		return npc_id
	for n in get_tree().get_nodes_in_group("npc"):
		if "animal_id" in n and n.animal_id == npc_id:
			return n.animal_name if "animal_name" in n else npc_id
	return npc_id


func _typing_in_textbox() -> bool:
	var f := get_viewport().gui_get_focus_owner()
	return f is LineEdit or f is TextEdit
