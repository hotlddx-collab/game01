@tool
extends CanvasLayer
## 竞选 HUD - 屏幕顶部中央常驻 + 投票日全屏结算演出
##
## 数据源：AgentClient.election_state_received（每天 22:00 后端推送 + 06:00 客户端拉取）
##         AgentClient.election_result_received（D7 投票日触发演出）

# ---- 布局配置（Inspector 可实时调）----
@export_group("HUD 布局")
## 面板左上角屏幕位置
@export var panel_margin_left: float = 16.0:
	set(v): panel_margin_left = v; _apply_layout()
@export var panel_margin_top: float = 16.0:
	set(v): panel_margin_top = v; _apply_layout()
## 面板宽高
@export var panel_width: float = 384.0:
	set(v): panel_width = v; _apply_layout()
@export var panel_height: float = 120.0:
	set(v): panel_height = v; _apply_layout()
## 面板内四边留白
@export var pad_left: float = 16.0:
	set(v): pad_left = v; _apply_layout()
@export var pad_right: float = 16.0:
	set(v): pad_right = v; _apply_layout()
@export var pad_top: float = 12.0:
	set(v): pad_top = v; _apply_layout()
@export var pad_bottom: float = 8.0:
	set(v): pad_bottom = v; _apply_layout()
## 行与行垂直间距
@export var row_separation: int = 6:
	set(v): row_separation = v; _apply_layout()
## 名字列宽 / 分数列宽 / 进度条最小长度 / 进度条高度
@export var name_column_width: float = 64.0:
	set(v): name_column_width = v; _apply_layout()
@export var score_column_width: float = 36.0:
	set(v): score_column_width = v; _apply_layout()
@export var bar_min_length: float = 230.0:
	set(v): bar_min_length = v; _apply_layout()
@export var bar_height: float = 16.0:
	set(v): bar_height = v; _apply_layout()
## 行内元素水平间距
@export var row_h_separation: int = 10:
	set(v): row_h_separation = v; _apply_layout()
## 字号
@export var font_size: int = 13:
	set(v): font_size = v; _apply_layout()

# ---- 常驻 HUD ----
@onready var panel: Panel = $Panel
@onready var _vbox: VBoxContainer = $Panel/VBox
@onready var header: RichTextLabel = %Header
@onready var player_name_lbl: Label = $Panel/VBox/PlayerRow/Name
@onready var player_bar: ProgressBar = $Panel/VBox/PlayerRow/Bar
@onready var player_score_lbl: Label = $Panel/VBox/PlayerRow/Score
@onready var opponent_name_lbl: Label = $Panel/VBox/OpponentRow/Name
@onready var opponent_bar: ProgressBar = $Panel/VBox/OpponentRow/Bar
@onready var opponent_score_lbl: Label = $Panel/VBox/OpponentRow/Score
@onready var hint_label: RichTextLabel = %Hint

# ---- 投票演出 overlay ----
@onready var result_overlay: ColorRect = %ResultOverlay
@onready var result_title: RichTextLabel = %Title
@onready var result_status: RichTextLabel = %Status
@onready var result_banner: RichTextLabel = %Banner
@onready var result_close_btn: Button = %CloseBtn
@onready var pvote_name: Label = $ResultOverlay/Center/ResultPanel/Margin/VBox/VotesBox/PlayerVote/Name
@onready var pvote_bar: ProgressBar = $ResultOverlay/Center/ResultPanel/Margin/VBox/VotesBox/PlayerVote/Bar
@onready var pvote_num: Label = $ResultOverlay/Center/ResultPanel/Margin/VBox/VotesBox/PlayerVote/Num
@onready var ovote_name: Label = $ResultOverlay/Center/ResultPanel/Margin/VBox/VotesBox/OpVote/Name
@onready var ovote_bar: ProgressBar = $ResultOverlay/Center/ResultPanel/Margin/VBox/VotesBox/OpVote/Bar
@onready var ovote_num: Label = $ResultOverlay/Center/ResultPanel/Margin/VBox/VotesBox/OpVote/Num

const SCORE_BAR_MAX: float = 200.0

var _last_view: Dictionary = {}
var _opponent_id: String = ""
var _promise_active: int = 0
var _promise_max: int = 5
var _player_incumbent: bool = false
var _player_power: int = 0
var _player_power_max: int = 0
var _incumbent_id: String = ""
var _day_theme: String = "campaign"

# ---- 当日主题横幅（运行时构建）----
var _day_banner: PanelContainer = null
var _day_banner_label: RichTextLabel = null
var _day_banner_tween: Tween = null


func _id_to_name(npc_id: String) -> String:
	if npc_id == "" or npc_id == "player":
		return "你"
	for n in get_tree().get_nodes_in_group("npc"):
		if "animal_id" in n and n.animal_id == npc_id:
			return n.animal_name if "animal_name" in n else npc_id
	return npc_id


func _ready() -> void:
	_apply_layout()
	if Engine.is_editor_hint():
		return
	# 默认占位文字
	header.text = "🗳 [b]竞选系统初始化…[/b]  [color=#888](等待后端)[/color]"
	hint_label.text = ""
	hint_label.visible = false
	result_overlay.visible = false
	result_close_btn.pressed.connect(_on_overlay_close)
	_build_day_banner()

	if not Engine.has_singleton("AgentClient") and AgentClient == null:
		push_warning("[ElectionHUD] AgentClient autoload 不存在")
		return

	if AgentClient.has_signal("election_state_received"):
		AgentClient.election_state_received.connect(_on_state)
		AgentClient.election_result_received.connect(_on_result)
		AgentClient.opponent_action_received.connect(_on_opponent_action)
		AgentClient.promise_state_received.connect(_on_promise_state)
		AgentClient.connected.connect(_on_connected)
	if AgentClient.has_signal("day_event_received"):
		AgentClient.day_event_received.connect(_on_day_event)

	# 启动后定时尝试拉数据
	for delay in [0.5, 2.0, 5.0]:
		await get_tree().create_timer(delay).timeout
		if _last_view.is_empty() and AgentClient.has_method("request_election_query"):
			AgentClient.request_election_query()
			AgentClient.request_promise_query()
		else:
			break


func _on_connected() -> void:
	AgentClient.request_election_query()
	AgentClient.request_promise_query()


func _build_day_banner() -> void:
	_day_banner = PanelContainer.new()
	_day_banner.name = "DayBanner"
	_day_banner.visible = false
	_day_banner.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(_day_banner)
	_day_banner.anchor_left = 0.5
	_day_banner.anchor_right = 0.5
	_day_banner.anchor_top = 0.0
	_day_banner.anchor_bottom = 0.0
	_day_banner.grow_horizontal = Control.GROW_DIRECTION_BOTH
	_day_banner.grow_vertical = Control.GROW_DIRECTION_END
	_day_banner.offset_top = 118.0

	var sb := StyleBoxFlat.new()
	sb.bg_color = Color(0.08, 0.09, 0.13, 0.92)
	sb.border_color = Color(0.78, 0.61, 0.31, 1.0)
	sb.set_border_width_all(2)
	sb.set_corner_radius_all(10)
	sb.content_margin_left = 22
	sb.content_margin_right = 22
	sb.content_margin_top = 12
	sb.content_margin_bottom = 12
	_day_banner.add_theme_stylebox_override("panel", sb)

	_day_banner_label = RichTextLabel.new()
	_day_banner_label.bbcode_enabled = true
	_day_banner_label.fit_content = true
	_day_banner_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_day_banner_label.scroll_active = false
	_day_banner_label.custom_minimum_size = Vector2(560, 0)
	_day_banner_label.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_day_banner.add_child(_day_banner_label)


func _on_day_event(info: Dictionary) -> void:
	if _day_banner == null:
		return
	var di := int(info.get("day_index", 1))
	var td := int(info.get("term_days", 4))
	var title := String(info.get("title", ""))
	var hint := String(info.get("hint", ""))
	_day_banner_label.text = "[center][b]%s[/b]  [color=#c69b50]· 第 %d/%d 天[/color]\n[color=#cdd6e6]%s[/color][/center]" % [
		title, di, td, hint
	]
	_show_banner(4.4)


## 镇务结果中央横幅（复用 day_banner）
func flash_mayor_toast(title: String, sub: String) -> void:
	if _day_banner == null:
		return
	var body := "[center][b]%s[/b]" % title
	if sub != "":
		body += "\n[color=#cdd6e6]%s[/color]" % sub
	body += "[/center]"
	_day_banner_label.text = body
	_show_banner(4.0)


## 显示横幅并在 total 秒后淡出
func _show_banner(total: float) -> void:
	_day_banner.visible = true
	_day_banner.modulate.a = 0.0
	if _day_banner_tween and _day_banner_tween.is_valid():
		_day_banner_tween.kill()
	_day_banner_tween = create_tween()
	_day_banner_tween.tween_property(_day_banner, "modulate:a", 1.0, 0.4)
	_day_banner_tween.tween_interval(max(0.5, total - 1.0))
	_day_banner_tween.tween_property(_day_banner, "modulate:a", 0.0, 0.6)
	_day_banner_tween.tween_callback(func() -> void: _day_banner.visible = false)


func _on_promise_state(info: Dictionary) -> void:
	_promise_active = int(info.get("active_count", 0))
	_promise_max = int(info.get("max_count", 5))
	_refresh()


func _on_state(view: Dictionary) -> void:
	var had_prev := not _last_view.is_empty()
	var prev_scores: Dictionary = _last_view.get("scores", {})
	var prev_opp := _opponent_id
	_last_view = view
	_opponent_id = String(view.get("opponent_id", ""))
	_player_incumbent = bool(view.get("player_incumbent", false))
	_player_power = int(view.get("player_power", 0))
	_player_power_max = int(view.get("player_power_max", 0))
	_incumbent_id = String(view.get("incumbent_id", ""))
	_day_theme = String(view.get("day_theme", "campaign"))
	_refresh()
	# 分数变动 → 在对应分数右侧飘小字，说明为什么变
	if not had_prev:
		return
	var evs: Array = view.get("belief_events", [])
	var scores: Dictionary = view.get("scores", {})
	var dp := int(round(float(scores.get("player", 0.0)) - float(prev_scores.get("player", 0.0))))
	var do_ := int(round(float(scores.get(_opponent_id, 0.0)) - float(prev_scores.get(prev_opp, 0.0))))
	if dp != 0:
		_float_delta(player_score_lbl, dp, _reason_text(evs, "player"))
	if do_ != 0:
		_float_delta(opponent_score_lbl, do_, _reason_text(evs, _opponent_id))


## 把归因事件压成一句短原因（如「小蓝信了谣言」/「焰仔护主」）
func _reason_text(evs: Array, subject_id: String) -> String:
	for e in evs:
		if not (e is Dictionary):
			continue
		if String(e.get("subject_id", "")) != subject_id:
			continue
		var listener := String(e.get("listener", "有人"))
		if String(e.get("kind", "")) == "believed":
			var s := String(e.get("sentiment", "smear"))
			return "%s信了%s" % [listener, "夸赞" if s == "praise" else "谣言"]
		else:
			return "%s不信(%s)" % [listener, String(e.get("reason", ""))]
	return ""


## 分数右侧小字飘动：上浮 + 淡出，不占布局空间
func _float_delta(anchor: Control, delta: int, reason: String) -> void:
	if anchor == null or delta == 0:
		return
	var lbl := RichTextLabel.new()
	lbl.bbcode_enabled = true
	lbl.fit_content = true
	lbl.scroll_active = false
	lbl.autowrap_mode = TextServer.AUTOWRAP_OFF
	lbl.mouse_filter = Control.MOUSE_FILTER_IGNORE
	lbl.custom_minimum_size = Vector2(150, 0)
	var col := "#5fd68a" if delta > 0 else "#ff8a6a"
	var sign := "+" if delta > 0 else ""
	var txt := "[color=%s]%s%d[/color]" % [col, sign, delta]
	if reason != "":
		txt += " [color=#b8b8b8]%s[/color]" % reason
	lbl.text = txt
	lbl.add_theme_font_size_override("normal_font_size", 11)
	panel.add_child(lbl)
	# 起点：贴在分数标签右侧（相对 panel 的局部坐标）
	var start := anchor.global_position - panel.global_position
	start.x += anchor.size.x + 6.0
	lbl.position = start
	var tw := create_tween().set_parallel(true)
	tw.tween_property(lbl, "position:y", start.y - 22.0, 1.4).set_ease(Tween.EASE_OUT)
	tw.tween_property(lbl, "modulate:a", 0.0, 1.4).set_delay(0.35)
	tw.chain().tween_callback(lbl.queue_free)


func _on_opponent_action(info: Dictionary) -> void:
	# 实际气泡显示交给 main.gd._on_opponent_action（找 NPC 节点）
	pass


func _refresh() -> void:
	if _last_view.is_empty():
		return

	var term_id := int(_last_view.get("term_id", 1))
	var day_idx := int(_last_view.get("day_index", 1))
	var term_days := int(_last_view.get("term_days", 4))
	var stage_text := _theme_label(_day_theme)
	var remaining: int = max(0, term_days - day_idx)

	# 第 1 行：政权状态（谁是镇长），第 2 行：进度 + 当日阶段
	var regime := _regime_line(term_id)
	var progress := "🗳 [color=#c69b50]D%d/%d · %s[/color]" % [day_idx, term_days, stage_text]
	if remaining > 0:
		progress += " [color=#888](投票倒计 %d 天)[/color]" % remaining
	else:
		progress += " [color=#ffce8a](今日投票)[/color]"
	header.text = "%s\n%s" % [regime, progress]

	var scores: Dictionary = _last_view.get("scores", {})
	var p_score := float(scores.get("player", 0.0))
	var o_score := float(scores.get(_opponent_id, 0.0))

	player_name_lbl.text = "👤 你"
	player_bar.max_value = SCORE_BAR_MAX
	player_bar.value = clamp(p_score, 0.0, SCORE_BAR_MAX)
	player_score_lbl.text = "%d" % int(round(p_score))

	opponent_name_lbl.text = "🐾 %s" % _id_to_name(_opponent_id)
	opponent_bar.max_value = SCORE_BAR_MAX
	opponent_bar.value = clamp(o_score, 0.0, SCORE_BAR_MAX)
	opponent_score_lbl.text = "%d" % int(round(o_score))

	# 承诺池已精简移除；镇长权力提示并到进度行右侧
	if _player_incumbent:
		header.text += "  [color=#ffd864]🏛权力%s/%s(K)[/color]" % [_player_power, _player_power_max]


## 面板尺寸固定（由 tscn 控制），内容 EXPAND 铺满；保留空壳供旧调用点
func _resize_panel() -> void:
	pass


## 按 @export 参数应用布局，Inspector 改动即时生效
func _apply_layout() -> void:
	if not is_inside_tree():
		return
	var p := get_node_or_null("Panel") as Panel
	if p == null:
		return
	p.offset_left = panel_margin_left
	p.offset_top = panel_margin_top
	p.offset_right = panel_margin_left + panel_width
	p.offset_bottom = panel_margin_top + panel_height

	var vb := p.get_node_or_null("VBox") as VBoxContainer
	if vb == null:
		return
	vb.offset_left = pad_left
	vb.offset_right = -pad_right
	vb.offset_top = pad_top
	vb.offset_bottom = -pad_bottom
	vb.add_theme_constant_override("separation", row_separation)

	var hdr := vb.get_node_or_null("Header") as RichTextLabel
	if hdr != null:
		hdr.add_theme_font_size_override("normal_font_size", font_size)
		hdr.add_theme_font_size_override("bold_font_size", font_size)

	for row_name in ["PlayerRow", "OpponentRow"]:
		var row := vb.get_node_or_null(row_name) as HBoxContainer
		if row == null:
			continue
		row.add_theme_constant_override("separation", row_h_separation)
		var nm := row.get_node_or_null("Name") as Label
		if nm != null:
			nm.custom_minimum_size = Vector2(name_column_width, 0)
			nm.add_theme_font_size_override("font_size", font_size)
		var bar := row.get_node_or_null("Bar") as ProgressBar
		if bar != null:
			bar.custom_minimum_size = Vector2(bar_min_length, bar_height)
		var sc := row.get_node_or_null("Score") as Label
		if sc != null:
			sc.custom_minimum_size = Vector2(score_column_width, 0)
			sc.add_theme_font_size_override("font_size", font_size)


## 顶栏第 1 行：按现任镇长身份区分开荒 / 卫冕 / 在野
func _regime_line(term_id: int) -> String:
	if _incumbent_id == "":
		return "🌱 [b]首届镇长竞选[/b]"
	elif _incumbent_id == "player":
		return "🏛 [b]现任镇长：你[/b] [color=#c69b50]· 第%d届[/color]" % term_id
	else:
		return "🏛 [b]现任镇长：%s[/b] [color=#ff9a9a]· 夺回[/color]" % _id_to_name(_incumbent_id)


func _theme_label(theme: String) -> String:
	match theme:
		"rally": return "📣 集会日"
		"debate": return "🎤 辩论日"
		"crisis": return "⚡ 危机日"
		"vote": return "🗳 投票日"
		_: return "🌿 竞选日"


func _gen_hint(_day_idx: int, p: float, o: float) -> String:
	if _day_theme == "debate":
		return "[color=#ffd27f]💬 今天是辩论日，记得参加广场辩论。[/color]"
	if _day_theme == "vote":
		return "[color=#ffd27f]🗳 今天是投票日，结果即将揭晓。[/color]"
	if _day_theme == "crisis":
		return "[color=#ffd27f]⚡ 镇上出了乱子，妥善调解能左右选情。[/color]"
	# 无镇长（开荒）/ 有镇长（卫冕/在野）分别给基调提示
	if _incumbent_id == "":
		return "[color=#d6e0a0]赢得选举，成为森林第一任镇长。[/color]"
	var diff := p - o
	if _incumbent_id == "player":
		if diff < 0.0:
			return "[color=#ffce8a]守住镇长位！挑战者 %s 正在追赶。[/color]" % _id_to_name(_opponent_id)
		return "[color=#8de89a]✓ 稳住民心，守好镇长之位。[/color]"
	# 在野：夺回
	if diff < -20.0:
		return "[color=#ff8a8a]⚠ 落后较多，多拜访 NPC 才能扳倒 %s。[/color]" % _id_to_name(_opponent_id)
	elif diff < 0.0:
		return "[color=#ffce8a]略微落后。送礼 / 兑现承诺，夺回镇长位。[/color]"
	elif diff < 20.0:
		return "[color=#d6e0a0]势均力敌，每次互动都在拉票。[/color]"
	else:
		return "[color=#8de89a]✓ 优势明显，镇长位就在眼前。[/color]"


# ============================================================
# 投票日全屏结算演出
# ============================================================

func _on_result(info: Dictionary) -> void:
	print("[ElectionHUD] 投票结束 winner=%s votes=%s" % [
		info.get("winner_id", ""), info.get("votes", {})
	])
	show_vote_result(info)


func show_vote_result(info: Dictionary) -> void:
	# 重置 UI 到初始状态
	var votes: Dictionary = info.get("votes", {})
	var p_target := int(votes.get("player", 0))
	var op_id := String(info.get("next_opponent_id", _opponent_id))
	# 找出对手在 votes 字典中的 key（结算时是上届对手）
	var settled_opponent := ""
	for k in votes.keys():
		if k != "player":
			settled_opponent = String(k)
			break
	var o_target := int(votes.get(settled_opponent, 0))

	result_title.text = "[center][b]🗳 第 %d 届投票日[/b][/center]" % int(info.get("settled_term_id", 0))
	result_status.text = "[center][color=#888]NPC 正在投出他们手中的票…[/color][/center]"
	result_banner.text = ""
	result_close_btn.visible = false

	pvote_name.text = "👤 你"
	pvote_num.text = "0"
	pvote_bar.value = 0.0

	ovote_name.text = "🐾 %s" % _id_to_name(settled_opponent)
	ovote_num.text = "0"
	ovote_bar.value = 0.0

	result_overlay.modulate.a = 0.0
	result_overlay.visible = true

	# 渐入
	var fade_in := create_tween()
	fade_in.tween_property(result_overlay, "modulate:a", 1.0, 0.4)

	# 票数滚动（玩家）
	var p_bar_tw := create_tween()
	p_bar_tw.tween_interval(0.6)
	p_bar_tw.tween_property(pvote_bar, "value", p_target, 1.4).set_trans(Tween.TRANS_CUBIC)

	var p_num_tw := create_tween()
	p_num_tw.tween_interval(0.6)
	p_num_tw.tween_method(_set_player_num, 0.0, float(p_target), 1.4).set_trans(Tween.TRANS_CUBIC)

	# 票数滚动（对手）
	var o_bar_tw := create_tween()
	o_bar_tw.tween_interval(0.7)
	o_bar_tw.tween_property(ovote_bar, "value", o_target, 1.4).set_trans(Tween.TRANS_CUBIC)

	var o_num_tw := create_tween()
	o_num_tw.tween_interval(0.7)
	o_num_tw.tween_method(_set_op_num, 0.0, float(o_target), 1.4).set_trans(Tween.TRANS_CUBIC)

	# 等动画完，显示横幅
	await get_tree().create_timer(2.6).timeout
	_show_result_banner(info, op_id)


func _set_player_num(v: float) -> void:
	if pvote_num:
		pvote_num.text = "%d" % int(round(v))


func _set_op_num(v: float) -> void:
	if ovote_num:
		ovote_num.text = "%d" % int(round(v))


func _show_result_banner(info: Dictionary, next_op_id: String) -> void:
	if not is_instance_valid(result_banner):
		return
	var winner := String(info.get("winner_id", ""))
	var tie := bool(info.get("tie_break", false))
	var next_op_name := _id_to_name(next_op_id)

	if winner == "player":
		result_banner.text = (
			"[center][color=#88ee88][b]🎉 你当选了！[/b][/color]\n"
			+ "[color=#dddddd]从明天起，你是这片森林新的镇长。[/color]\n"
			+ "[color=#888888](下一任挑战者：%s)[/color][/center]"
		) % next_op_name
		if result_status:
			result_status.text = "[center][color=#88ee88]胜负已分[/color][/center]"
	else:
		var op_name := _id_to_name(winner)
		var tie_note := "（票数相同，按规则你败）" if tie else ""
		result_banner.text = (
			"[center][color=#ee8888][b]😔 你落选了。[/b][/color]\n"
			+ "[color=#dddddd]%s 当选了本届镇长。%s[/color]\n"
			+ "[color=#888888](下届继续挑战现任镇长：%s)[/color][/center]"
		) % [op_name, tie_note, next_op_name]
		if result_status:
			result_status.text = "[center][color=#ee8888]胜负已分[/color][/center]"

	result_close_btn.visible = true


func _on_overlay_close() -> void:
	if not is_instance_valid(result_overlay):
		return
	var tw := create_tween()
	tw.tween_property(result_overlay, "modulate:a", 0.0, 0.3)
	await tw.finished
	if is_instance_valid(result_overlay):
		result_overlay.visible = false
		result_overlay.modulate.a = 1.0
