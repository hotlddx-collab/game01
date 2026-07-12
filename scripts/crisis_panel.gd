extends CanvasLayer
## 危机调解（镇长政务玩法）——世界演出 + 调解面板一体控制器
##
## 体验三段式：
##   A. 触发：两只当事 NPC 走到一起、头顶挂💢、交替冒气泡吵架；顶部横幅导航。
##   B. 交互：玩家走到任一当事 NPC 旁按 E → main.gd 调 open_panel() 打开调解面板。
##   C. 反馈：裁决后受益方❤️/😊+感谢、受损方😠/💔+不满，延时散场归日程。
## 数据源：AgentClient.crisis_state_received / crisis_result_received

@onready var banner: PanelContainer = $Banner
@onready var banner_label: RichTextLabel = %BannerLabel
@onready var backdrop: ColorRect = $Backdrop
@onready var title: RichTextLabel = %Title
@onready var summary: RichTextLabel = %Summary
@onready var statements_vbox: VBoxContainer = %StatementsVBox
@onready var options_vbox: VBoxContainer = %OptionsVBox
@onready var result_label: RichTextLabel = %ResultLabel

var _view: Dictionary = {}       # 当前危机视图
var _crisis_id: int = -1
var _busy: bool = false           # resolve 在途
var _resolving: bool = false      # 已提交裁决、正在演出结果
var _staged: bool = false         # 世界演出（聚集）已启动
var _gathered: bool = false       # 两只当事 NPC 已会合面对面
var _gather_count: int = 0        # 已到达会合点的当事人数
var _requested_statements: bool = false
var _argue_idx: int = 0

var _argue_timer: Timer = null
var _emote_timer: Timer = null


func _ready() -> void:
	backdrop.visible = false
	banner.visible = false
	_argue_timer = Timer.new()
	_argue_timer.wait_time = 4.0
	_argue_timer.timeout.connect(_on_argue_tick)
	add_child(_argue_timer)
	_emote_timer = Timer.new()
	_emote_timer.wait_time = 2.2
	_emote_timer.timeout.connect(_on_emote_tick)
	add_child(_emote_timer)

	if AgentClient.has_signal("crisis_state_received"):
		AgentClient.crisis_state_received.connect(_on_state)
		AgentClient.crisis_result_received.connect(_on_result)
	# 进游戏后拉一次（可能有未处理的危机）
	call_deferred("_initial_query")


func _initial_query() -> void:
	await get_tree().create_timer(1.0).timeout
	if AgentClient.is_connected_to_server():
		AgentClient.request_crisis_query(false)


func _input(event: InputEvent) -> void:
	if _typing_in_textbox():
		return
	if event is InputEventKey and event.pressed and not event.echo:
		if event.keycode == KEY_J and event.ctrl_pressed:
			# 调试：立即触发一个危机
			if AgentClient.has_method("request_debug_spawn_crisis"):
				AgentClient.request_debug_spawn_crisis()
			get_viewport().set_input_as_handled()
		elif event.keycode == KEY_ESCAPE and backdrop.visible:
			_close_panel()
			get_viewport().set_input_as_handled()


# ──────────────────────────────────────────────
# 对外接口（main.gd 调用）
# ──────────────────────────────────────────────

## 该 NPC 是否为当前危机当事人（供 main 判断按 E 是否进调解）
## 要求两只已会合（走到现场）后才允许调解
func is_party(npc_id: String) -> bool:
	if _crisis_id < 0 or _resolving or _view.is_empty() or not _gathered:
		return false
	for p in _view.get("parties", []):
		if String(p.get("npc_id", "")) == npc_id:
			return true
	return false


## 打开调解面板（玩家走到现场按 E 触发）
func open_panel() -> void:
	if _view.is_empty():
		return
	backdrop.visible = true
	result_label.text = ""
	# 确保双方说法已生成
	if _statements_empty():
		AgentClient.request_crisis_query(true)
	_render()


# ──────────────────────────────────────────────
# 后端状态
# ──────────────────────────────────────────────

func _on_state(info: Dictionary) -> void:
	var active := bool(info.get("active", false))
	if not active:
		if _resolving:
			return   # 结果演出中，忽略随后的空状态
		_reset_crisis()
		return

	var view: Dictionary = info.get("crisis", {})
	var cid := int(view.get("crisis_id", -1))
	if cid != _crisis_id:
		# 新危机
		_reset_crisis(false)
		_crisis_id = cid
		_view = view
		_stage_crisis()
	else:
		# 同一危机的补充推送（通常带来了双方说法）
		_view = view
	# 说法到位后开始/刷新吵架气泡
	if not _statements_empty():
		_start_argue()
	elif not _requested_statements:
		_requested_statements = true
		AgentClient.request_crisis_query(true)
	if backdrop.visible:
		_render()


# ──────────────────────────────────────────────
# A. 世界演出：聚集 + 吵架
# ──────────────────────────────────────────────

func _stage_crisis() -> void:
	if _staged:
		return
	var parties: Array = _view.get("parties", [])
	if parties.size() < 2:
		_update_banner()
		return
	var a := _find_npc(String(parties[0].get("npc_id", "")))
	var b := _find_npc(String(parties[1].get("npc_id", "")))
	_staged = true
	_gathered = false
	_gather_count = 0
	_update_banner()
	if a == null or b == null:
		push_warning("[crisis] 缺人 a=%s b=%s parties=%s" % [a, b, parties])
		# 缺人：无法演出会合，仍标记 gathered 让面板可用
		_gathered = true
		_start_argue()
		return
	# 锁定：期间不被 ChatManager 拉去闲聊
	a.add_to_group("crisis_party")
	b.add_to_group("crisis_party")
	# 会合点：优先用危机 scene 对应地点（建筑入口，必然可达）；否则退回两者中点
	var meet: Vector2 = _scene_pos()
	if meet == Vector2.ZERO:
		meet = (a.global_position + b.global_position) * 0.5
	var dir: Vector2 = (b.global_position - a.global_position)
	dir = dir.normalized() if dir.length() > 1.0 else Vector2.RIGHT
	var gap := 20.0
	var a_target: Vector2 = meet - dir * gap
	var b_target: Vector2 = meet + dir * gap
	print("[crisis] 聚集 scene=%s meet=%s a(%s)@%s→%s b(%s)@%s→%s" % [
		String(_view.get("scene","")), meet,
		a.animal_id, a.global_position, a_target,
		b.animal_id, b.global_position, b_target])
	_walk_to_meet(a, a_target, b_target)
	_walk_to_meet(b, b_target, a_target)
	_start_gather_watchdog()


## 兜底：超时仍未会合也放行调解，避免卡死无法处理
func _start_gather_watchdog() -> void:
	var cid := _crisis_id
	await get_tree().create_timer(20.0).timeout
	if cid == _crisis_id and not _gathered:
		push_warning("[crisis] 会合超时，强制放行 cid=%d" % cid)
		_gathered = true
		_update_banner()
		_start_argue()


## 危机 scene（中文标签）→ 地点坐标，未找到返回 ZERO
func _scene_pos() -> Vector2:
	var scene := String(_view.get("scene", ""))
	if scene == "" or not has_node("/root/LocationDB"):
		return Vector2.ZERO
	var locs: Dictionary = LocationDB.all_locations()
	for id in locs:
		var info: Dictionary = locs[id]
		if String(info.get("label", "")) == scene:
			return info.get("position", Vector2.ZERO)
	return Vector2.ZERO


## npc 走到 my_target 会合；到达后精确贴位、面向对方落点，并累计会合人数
func _walk_to_meet(npc: Node, my_target: Vector2, other_target: Vector2) -> void:
	if npc.has_method("clear_busy"):
		npc.clear_busy()   # 解除日程/旧忙碌，确保 intent 能驱动移动
	if npc.has_method("approach_for_intent"):
		npc.approach_for_intent(my_target, func():
			if not is_instance_valid(npc):
				return
			print("[crisis] 到达 %s @%s" % [npc.animal_id, npc.global_position])
			npc.set_busy(npc.BusyState.TALKING_NPC, 999.0)
			# INTENT_ARRIVE_DIST 较大，会提前停；用短 tween 精确贴到会合点，保证两人紧挨
			var tw := create_tween()
			tw.tween_property(npc, "global_position", my_target, 0.35)
			npc.face_to(other_target)
			_on_gathered()
		)
	else:
		if npc.has_method("set_busy"):
			npc.set_busy(npc.BusyState.TALKING_NPC, 999.0)
		_on_gathered()


func _on_gathered() -> void:
	_gather_count += 1
	if _gather_count >= 2:
		_gathered = true
		_update_banner()
		_start_argue()


func _start_argue() -> void:
	if _resolving or not _gathered or _statements_empty():
		return
	if _emote_timer.is_stopped():
		_emote_timer.start()
		_on_emote_tick()
	if _argue_timer.is_stopped():
		_argue_timer.start()
		_on_argue_tick()


func _on_emote_tick() -> void:
	# 两只当事 NPC 头顶循环挂 💢
	for p in _view.get("parties", []):
		var n := _find_npc(String(p.get("npc_id", "")))
		if n and n.has_method("show_emote"):
			n.show_emote("💢", 2.4, 0.0)


func _on_argue_tick() -> void:
	var parties: Array = _view.get("parties", [])
	if parties.size() < 2:
		return
	var statements = _view.get("statements", {})
	# 交替：偶数轮 A 说、奇数轮 B 说
	var who: Dictionary = parties[_argue_idx % 2]
	_argue_idx += 1
	var nid := String(who.get("npc_id", ""))
	var n := _find_npc(nid)
	if n == null or not n.has_method("show_speech_bubble"):
		return
	var line := ""
	if typeof(statements) == TYPE_DICTIONARY:
		line = String(statements.get(nid, ""))
	if line == "":
		line = "（气呼呼地等镇长评理）"
	n.show_speech_bubble(line, 3.6)


func _stop_world_fx() -> void:
	_argue_timer.stop()
	_emote_timer.stop()


# ──────────────────────────────────────────────
# B. 调解面板 UI
# ──────────────────────────────────────────────

func _close_panel() -> void:
	backdrop.visible = false


func _render() -> void:
	if _view.is_empty():
		return
	title.text = "[center][b]⚖ %s[/b][/center]" % String(_view.get("title", "纠纷"))
	var scene := String(_view.get("scene", ""))
	summary.text = "[color=#e8dcc0]%s[/color]\n[color=#9a8] 地点：%s[/color]" % [String(_view.get("summary", "")), scene]

	for c in statements_vbox.get_children():
		c.queue_free()
	var statements = _view.get("statements", {})
	for p in _view.get("parties", []):
		var pid := String(p.get("npc_id", ""))
		var pname := String(p.get("name", pid))
		var say := ""
		if typeof(statements) == TYPE_DICTIONARY:
			say = String(statements.get(pid, ""))
		if say == "":
			say = "（还在气头上……）"
		var lbl := RichTextLabel.new()
		lbl.bbcode_enabled = true
		lbl.fit_content = true
		lbl.scroll_active = false
		lbl.custom_minimum_size = Vector2(540, 0)
		lbl.text = "[color=#ffcf8a][b]%s[/b][/color]：%s" % [pname, say]
		statements_vbox.add_child(lbl)

	for c in options_vbox.get_children():
		c.queue_free()
	for o in _view.get("options", []):
		_add_option_row(o)


func _add_option_row(o: Dictionary) -> void:
	var oid := String(o.get("id", ""))
	var label := String(o.get("label", oid))
	var tag := String(o.get("tag", ""))
	var requires = o.get("requires", {})

	var need_item := ""
	var need_count := 0
	if typeof(requires) == TYPE_DICTIONARY and requires.has("item"):
		need_item = String(requires.get("item", ""))
		need_count = int(requires.get("count", 1))

	var affordable := true
	var req_hint := ""
	if need_item != "":
		var have := 0
		if has_node("/root/PlayerInventory"):
			have = PlayerInventory.get_count(need_item)
		affordable = have >= need_count
		var iname := _item_label(need_item)
		req_hint = "  [color=%s](需 %s×%d，你有 %d)[/color]" % [
			("#9bd" if affordable else "#e88"), iname, need_count, have]

	var btn := Button.new()
	btn.text = "%s" % label
	btn.disabled = (not affordable) or _busy
	btn.pressed.connect(_on_option_pressed.bind(oid))
	options_vbox.add_child(btn)

	if tag != "" or req_hint != "":
		var hint := RichTextLabel.new()
		hint.bbcode_enabled = true
		hint.fit_content = true
		hint.scroll_active = false
		hint.custom_minimum_size = Vector2(540, 0)
		hint.text = "[color=#aa9]〔%s〕[/color]%s" % [tag, req_hint]
		options_vbox.add_child(hint)


func _on_option_pressed(option_id: String) -> void:
	if _busy or _view.is_empty():
		return
	_busy = true
	result_label.text = "[color=#888]裁决中…[/color]"
	var inv: Dictionary = {}
	if has_node("/root/PlayerInventory"):
		inv = PlayerInventory.get_all()
	if AgentClient.has_method("request_crisis_resolve"):
		AgentClient.request_crisis_resolve(_crisis_id, option_id, inv)


# ──────────────────────────────────────────────
# C. 结果演出
# ──────────────────────────────────────────────

func _on_result(info: Dictionary) -> void:
	_busy = false
	if not bool(info.get("ok", false)):
		result_label.text = "[color=#ee8888]✗ %s[/color]" % String(info.get("error", "调解失败"))
		return

	_resolving = true
	_stop_world_fx()
	# 消耗道具（后端已结算数值，前端同步扣背包）
	var consume = info.get("consume", {})
	if typeof(consume) == TYPE_DICTIONARY and consume.has("item"):
		if has_node("/root/PlayerInventory"):
			PlayerInventory.remove_item(String(consume.get("item", "")), int(consume.get("count", 1)))

	# 面板内文字反馈
	var reactions = info.get("reactions", {})
	var lines: Array = []
	if typeof(reactions) == TYPE_DICTIONARY:
		for npc_id in reactions:
			lines.append("[b]%s[/b]：%s" % [_name_of(npc_id), String(reactions[npc_id])])
	var tag := String(info.get("tag", ""))
	var expired := bool(info.get("expired", false))
	var t := String(info.get("title", ""))
	var head := ""
	if expired:
		head = "[color=#e0a060]⌛ 你没能及时处理「%s」，镇上议论纷纷。[/color]" % t
	else:
		head = "[color=#88ee88]✓ 你[b]%s[/b]地处理了「%s」[/color]" % [tag, t]
	result_label.text = head + "\n" + "\n".join(lines)
	for c in options_vbox.get_children():
		c.queue_free()

	# 世界内演出：受影响 NPC 情绪 + 反应气泡
	_perform_result(info)

	# 横幅收尾 + 延时散场
	if expired:
		banner_label.text = "[color=#e0a060]⌛ 错过了「%s」— 镇长失职[/color]" % t
	else:
		banner_label.text = "[color=#9be29b]✓ 已处理「%s」[/color]" % t
	banner.visible = true
	_finish_after(4.5)


func _perform_result(info: Dictionary) -> void:
	var reactions = info.get("reactions", {})
	# 用 affected 的 delta 决定情绪；无 affected 时退回按反应文本
	var delta_map: Dictionary = {}
	for a in info.get("affected", []):
		delta_map[String(a.get("npc_id", ""))] = int(a.get("delta", 0))

	for p in _view.get("parties", []):
		var nid := String(p.get("npc_id", ""))
		var n := _find_npc(nid)
		if n == null:
			continue
		var d := int(delta_map.get(nid, 0))
		if n.has_method("show_emote"):
			if d >= 5:
				n.show_emote("❤️", 2.5, 0.0)
			elif d > 0:
				n.show_emote("😊", 2.0, 0.0)
			elif d <= -5:
				n.show_emote("😠", 2.5, 0.0)
			elif d < 0:
				n.show_emote("😞", 2.0, 0.0)
			else:
				n.show_emote("🙂", 2.0, 0.0)
		var say := ""
		if typeof(reactions) == TYPE_DICTIONARY:
			say = String(reactions.get(nid, ""))
		if say != "" and n.has_method("show_speech_bubble"):
			n.show_speech_bubble(say, 4.0)


func _finish_after(delay: float) -> void:
	await get_tree().create_timer(delay).timeout
	# 当事 NPC 散场、恢复日程
	for p in _view.get("parties", []):
		var n := _find_npc(String(p.get("npc_id", "")))
		if n and n.has_method("clear_busy"):
			n.clear_busy()
	# 横幅淡出
	if banner.visible:
		var tw := create_tween()
		tw.tween_property(banner, "modulate:a", 0.0, 0.6)
		await tw.finished
		banner.visible = false
		banner.modulate.a = 1.0
	_reset_crisis()


# ──────────────────────────────────────────────
# 收尾 / 工具
# ──────────────────────────────────────────────

func _reset_crisis(hide_banner: bool = true) -> void:
	_stop_world_fx()
	# 若仍有当事人被冻结/在途，解除，避免卡死在原地
	for p in _view.get("parties", []):
		var n := _find_npc(String(p.get("npc_id", "")))
		if n:
			if n.is_in_group("crisis_party"):
				n.remove_from_group("crisis_party")
			if n.has_method("clear_busy"):
				n.clear_busy()
	_crisis_id = -1
	_view = {}
	_staged = false
	_gathered = false
	_gather_count = 0
	_resolving = false
	_requested_statements = false
	_argue_idx = 0
	_close_panel()
	if hide_banner:
		banner.visible = false
		banner.modulate.a = 1.0


func _update_banner() -> void:
	if _view.is_empty():
		banner.visible = false
		return
	var t := String(_view.get("title", "镇上出事了"))
	var scene := String(_view.get("scene", ""))
	var who := ""
	var parties: Array = _view.get("parties", [])
	if parties.size() >= 2:
		who = "%s 和 %s" % [String(parties[0].get("name", "")), String(parties[1].get("name", ""))]
	var loc := ("在【%s】" % scene) if scene != "" else ""
	if _gathered:
		banner_label.text = "[color=#ffd28a]⚠ %s%s 吵起来了 · [b]%s[/b][/color] [color=#cfa]— 走过去按 [b]E[/b] 调解[/color]" % [who, loc, t]
	else:
		banner_label.text = "[color=#ffd28a]⚠ %s 正赶往%s 理论 · [b]%s[/b][/color] [color=#cfa]— 稍等他们到场[/color]" % [who, loc, t]
	banner.modulate.a = 1.0
	banner.visible = true


func _statements_empty() -> bool:
	var s = _view.get("statements", {})
	return typeof(s) != TYPE_DICTIONARY or s.is_empty()


func _find_npc(npc_id: String) -> Node:
	if npc_id == "":
		return null
	for n in get_tree().get_nodes_in_group("npc"):
		if n is Animal and n.animal_id == npc_id:
			return n
	return null


func _name_of(npc_id: String) -> String:
	for p in _view.get("parties", []):
		if String(p.get("npc_id", "")) == npc_id:
			return String(p.get("name", npc_id))
	return npc_id


func _item_label(item_id: String) -> String:
	if has_node("/root/ItemDB") and ItemDB.has_method("get_item_name"):
		return ItemDB.get_item_name(item_id)
	return item_id


func _typing_in_textbox() -> bool:
	var f := get_viewport().gui_get_focus_owner()
	return f is LineEdit or f is TextEdit
