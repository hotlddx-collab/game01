extends CanvasLayer
## 镇长权力面板（按 K 键打开 / 关闭）
##
## 仅在玩家为现任镇长时可用。展示每日权力点 + 行动菜单：
##   巡视拜访（need_target）→ 选一个 NPC
##   发布公告（无目标）→ 全体小幅加好感
## 数据源：AgentClient.power_state_received / power_result_received

@onready var backdrop: ColorRect = $Backdrop
@onready var title: RichTextLabel = %Title
@onready var power_label: RichTextLabel = %PowerLabel
@onready var actions_vbox: VBoxContainer = %ActionsVBox
@onready var result_label: RichTextLabel = %ResultLabel
@onready var help_label: RichTextLabel = %HelpLabel

var _state: Dictionary = {}
var _busy: bool = false
var _expanded_action: String = ""   # 当前展开目标选择的行动 id


func _ready() -> void:
	backdrop.visible = false
	if AgentClient.has_signal("power_state_received"):
		AgentClient.power_state_received.connect(_on_state)
		AgentClient.power_result_received.connect(_on_result)


func _input(event: InputEvent) -> void:
	if _typing_in_textbox():
		return
	if event is InputEventKey and event.pressed and not event.echo:
		if event.keycode == KEY_K and not event.ctrl_pressed:
			_toggle()
			get_viewport().set_input_as_handled()
		elif event.keycode == KEY_K and event.ctrl_pressed:
			# 调试：强制授权现任
			if AgentClient.has_method("request_debug_grant_power"):
				AgentClient.request_debug_grant_power()
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
	result_label.text = ""
	if AgentClient.has_method("request_power_query"):
		AgentClient.request_power_query()
	if not _state.is_empty():
		_render()


func _close() -> void:
	backdrop.visible = false
	_expanded_action = ""


func _on_state(info: Dictionary) -> void:
	_state = info
	if backdrop.visible:
		_render()


func _on_result(info: Dictionary) -> void:
	_busy = false
	if bool(info.get("ok", false)):
		var txt := String(info.get("text", ""))
		result_label.text = "[color=#88ee88]✓ %s[/color]" % txt
		_expanded_action = ""
	else:
		var err := String(info.get("error", "行动失败"))
		result_label.text = "[color=#ee8888]✗ %s[/color]" % err
	# power_state 会随后被后端推送刷新点数


func _render() -> void:
	for c in actions_vbox.get_children():
		c.queue_free()

	var incumbent := bool(_state.get("incumbent", false))
	if not incumbent:
		power_label.text = "[center][color=#cc8888]你还不是镇长。赢得选举后才能行使权力。[/color][/center]"
		var dbg := Button.new()
		dbg.text = "🔧 调试：直接授予镇长权力 (Ctrl+K)"
		dbg.pressed.connect(func():
			if AgentClient.has_method("request_debug_grant_power"):
				AgentClient.request_debug_grant_power()
		)
		actions_vbox.add_child(dbg)
		return

	var power := int(_state.get("power", 0))
	var power_max := int(_state.get("power_max", 3))
	power_label.text = "[center]今日权力点：%s[/center]" % _dots(power, power_max)

	var actions: Array = _state.get("actions", [])
	for a in actions:
		_add_action_row(a, power)


func _dots(cur: int, total: int) -> String:
	var s := ""
	for i in range(total):
		if i < cur:
			s += "[color=#ffd864]●[/color] "
		else:
			s += "[color=#555]○[/color] "
	return "[b]%s[/b] [color=#aaa](%d/%d)[/color]" % [s, cur, total]


func _add_action_row(a: Dictionary, power: int) -> void:
	var aid := String(a.get("id", ""))
	var label := String(a.get("label", aid))
	var cost := int(a.get("cost", 1))
	var need_target := bool(a.get("need_target", false))
	var desc := String(a.get("desc", ""))
	var affordable := power >= cost and not _busy

	var box := PanelContainer.new()
	var sb := StyleBoxFlat.new()
	sb.bg_color = Color(0.20, 0.16, 0.10, 0.9)
	sb.border_color = Color(0.6, 0.5, 0.3, 0.6)
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
	vb.add_theme_constant_override("separation", 6)
	pad.add_child(vb)

	var info := RichTextLabel.new()
	info.bbcode_enabled = true
	info.fit_content = true
	info.scroll_active = false
	info.custom_minimum_size = Vector2(0, 40)
	info.text = "[b]%s[/b]  [color=#ffd864](%d 点)[/color]\n[color=#bbb]%s[/color]" % [label, cost, desc]
	vb.add_child(info)

	var btn := Button.new()
	if need_target:
		btn.text = "选择目标…" if _expanded_action != aid else "收起"
	else:
		btn.text = "执行"
	btn.disabled = not affordable
	btn.pressed.connect(_on_action_pressed.bind(aid, need_target))
	vb.add_child(btn)

	# 展开的目标选择
	if need_target and _expanded_action == aid:
		var grid := GridContainer.new()
		grid.columns = 3
		grid.add_theme_constant_override("h_separation", 6)
		grid.add_theme_constant_override("v_separation", 6)
		var targets: Array = _state.get("targets", [])
		for t in targets:
			var tid := String(t.get("npc_id", ""))
			var tname := String(t.get("name", tid))
			var tbtn := Button.new()
			tbtn.text = tname
			tbtn.disabled = not affordable
			tbtn.pressed.connect(_on_target_pressed.bind(aid, tid))
			grid.add_child(tbtn)
		vb.add_child(grid)

	actions_vbox.add_child(box)


func _on_action_pressed(aid: String, need_target: bool) -> void:
	if need_target:
		_expanded_action = "" if _expanded_action == aid else aid
		_render()
	else:
		_do_action(aid, "")


func _on_target_pressed(aid: String, target_id: String) -> void:
	_do_action(aid, target_id)


func _do_action(aid: String, target_id: String) -> void:
	if _busy:
		return
	_busy = true
	result_label.text = "[color=#888]执行中…[/color]"
	if AgentClient.has_method("request_power_action"):
		AgentClient.request_power_action(aid, target_id)


## 玩家正在输入框打字（LineEdit/TextEdit 聚焦）→ 快捷键应让位
func _typing_in_textbox() -> bool:
	var f := get_viewport().gui_get_focus_owner()
	return f is LineEdit or f is TextEdit
