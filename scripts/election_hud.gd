@tool
extends CanvasLayer
## 竞选 HUD - 屏幕顶部中央常驻 + 投票日全屏结算演出
##
## 数据源：AgentClient.election_state_received（每天 22:00 后端推送 + 06:00 客户端拉取）
##         AgentClient.election_result_received（D7 投票日触发演出）

## 唱票动画播完、胜负已分 → main.gd 接手换届黑幕过渡（叙事文案 + 换人 + 跳到早晨）
signal term_settled(info: Dictionary, next_opponent_id: String)

# ---- 布局配置（Inspector 可实时调）----
@export_group("HUD 布局")
## 面板左上角屏幕位置
@export var panel_margin_left: float = 16.0:
	set(v): panel_margin_left = v; _apply_layout()
@export var panel_margin_top: float = 16.0:
	set(v): panel_margin_top = v; _apply_layout()
## 面板宽高
@export var panel_width: float = 420.0:
	set(v): panel_width = v; _apply_layout()
## 面板最小高度；实际高度由内容自适应撑开（见 _resize_panel）
@export var panel_height: float = 112.0:
	set(v): panel_height = v; _apply_layout()
## 面板内四边留白
@export var pad_left: float = 14.0:
	set(v): pad_left = v; _apply_layout()
@export var pad_right: float = 14.0:
	set(v): pad_right = v; _apply_layout()
@export var pad_top: float = 10.0:
	set(v): pad_top = v; _apply_layout()
@export var pad_bottom: float = 10.0:
	set(v): pad_bottom = v; _apply_layout()
## 行与行垂直间距
@export var row_separation: int = 5:
	set(v): row_separation = v; _apply_layout()
## 名字列宽 / 分数列宽 / 进度条最小长度 / 进度条高度
@export var name_column_width: float = 70.0:
	set(v): name_column_width = v; _apply_layout()
@export var score_column_width: float = 52.0:
	set(v): score_column_width = v; _apply_layout()
@export var bar_min_length: float = 200.0:
	set(v): bar_min_length = v; _apply_layout()
@export var bar_height: float = 14.0:
	set(v): bar_height = v; _apply_layout()
## 行内元素水平间距
@export var row_h_separation: int = 8:
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

# 对手最近一批动作（visit/promise/smear/poach），供下一次 election_state
# 推送时做「为什么加/减分」的精确归因——这是真实发生的动作，比猜权重子项准得多。
# 用完即清空，不会串到下一次无关的分数变动上。
var _pending_opponent_actions: Array = []

# ---- 当日主题横幅（运行时构建）----
var _day_banner: PanelContainer = null
var _day_banner_label: RichTextLabel = null
var _day_banner_tween: Tween = null

# ---- 竞选增减记录日志（运行时构建，常驻可点开）----
const _GROWTH_LOG_MAX := 60
const _REASON_LABEL := {
	"affection": "好感",
	"promise": "承诺",
	"debate": "辩论",
	"event": "舆论事件",
	"loyalty": "人情站队",
	"incumbency": "执政包袱",
	"canvass": "拉票承诺",
}
var _log_btn: Button = null
var _log_panel: PanelContainer = null
var _log_list: VBoxContainer = null
var _growth_log: Array = [] # [{day:int, time:String, name:String, delta:int, reason:String}]
var _id_name_map: Dictionary = {} # animal_id -> 中文名，来自 roster 包，不依赖 NPC 节点是否已生成


func _id_to_name(npc_id: String) -> String:
	if npc_id == "" or npc_id == "player":
		return "你"
	if _id_name_map.has(npc_id) and String(_id_name_map[npc_id]) != "":
		return String(_id_name_map[npc_id])
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
	_build_growth_log_ui()

	if not Engine.has_singleton("AgentClient") and AgentClient == null:
		push_warning("[ElectionHUD] AgentClient autoload 不存在")
		return

	if AgentClient.has_signal("election_state_received"):
		AgentClient.election_state_received.connect(_on_state)
		AgentClient.election_result_received.connect(_on_result)
		AgentClient.opponent_action_received.connect(_on_opponent_action)
		AgentClient.promise_state_received.connect(_on_promise_state)
		AgentClient.connected.connect(_on_connected)
	if AgentClient.has_signal("roster_received"):
		AgentClient.roster_received.connect(_on_roster_received)
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


func _on_roster_received(present: Array) -> void:
	## roster 包本身就带中文名（{animal_id, name, ...}），直接建 id→名字表，
	## 不必等 NPC 节点生成——这样无论 election_state 和 roster 谁先到，
	## _id_to_name 都能立刻查到中文名，不会露出英文 id（如 bear_baker）。
	for e in present:
		if e is Dictionary:
			var aid := String(e.get("animal_id", ""))
			var nm := String(e.get("name", ""))
			if aid != "" and nm != "":
				_id_name_map[aid] = nm
	_refresh()


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


## 面板右上角的小按钮 + 常驻记录面板：谁的分数何时变了多少、为什么变。
## 分数右侧飘字转瞬即逝看不清，这里留一份可回看的完整记录。
func _build_growth_log_ui() -> void:
	_log_btn = Button.new()
	_log_btn.name = "GrowthLogBtn"
	_log_btn.text = "📜"
	_log_btn.tooltip_text = "竞选增减记录"
	_log_btn.custom_minimum_size = Vector2(26, 26)
	_log_btn.focus_mode = Control.FOCUS_NONE
	_log_btn.anchor_left = 1.0
	_log_btn.anchor_right = 1.0
	_log_btn.grow_horizontal = Control.GROW_DIRECTION_BEGIN
	_log_btn.offset_left = -34.0
	_log_btn.offset_right = -8.0
	_log_btn.offset_top = 8.0
	_log_btn.offset_bottom = 34.0
	panel.add_child(_log_btn)
	_log_btn.pressed.connect(_toggle_growth_log)

	_log_panel = PanelContainer.new()
	_log_panel.name = "GrowthLogPanel"
	_log_panel.visible = false
	_log_panel.custom_minimum_size = Vector2(300, 0)
	add_child(_log_panel)

	var sb := StyleBoxFlat.new()
	sb.bg_color = Color(0.08, 0.09, 0.13, 0.95)
	sb.border_color = Color(0.78, 0.61, 0.31, 1.0)
	sb.set_border_width_all(2)
	sb.set_corner_radius_all(10)
	sb.content_margin_left = 14
	sb.content_margin_right = 14
	sb.content_margin_top = 10
	sb.content_margin_bottom = 10
	_log_panel.add_theme_stylebox_override("panel", sb)

	var vb := VBoxContainer.new()
	vb.add_theme_constant_override("separation", 6)
	_log_panel.add_child(vb)

	var title_row := HBoxContainer.new()
	vb.add_child(title_row)
	var title_lbl := Label.new()
	title_lbl.text = "📜 竞选增减记录"
	title_lbl.add_theme_font_size_override("font_size", 14)
	title_row.add_child(title_lbl)
	var spacer := Control.new()
	spacer.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	title_row.add_child(spacer)
	var close_btn := Button.new()
	close_btn.text = "✕"
	close_btn.custom_minimum_size = Vector2(22, 22)
	close_btn.focus_mode = Control.FOCUS_NONE
	close_btn.pressed.connect(_toggle_growth_log)
	title_row.add_child(close_btn)

	var scroll := ScrollContainer.new()
	scroll.custom_minimum_size = Vector2(300, 260)
	vb.add_child(scroll)

	_log_list = VBoxContainer.new()
	_log_list.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_log_list.add_theme_constant_override("separation", 3)
	scroll.add_child(_log_list)


func _toggle_growth_log() -> void:
	if _log_panel == null:
		return
	_log_panel.visible = not _log_panel.visible
	if _log_panel.visible:
		_position_growth_log()
		_render_growth_log()


## 贴在常驻面板正下方，跟随面板实际高度（内容自适应会变高）
func _position_growth_log() -> void:
	if panel == null or _log_panel == null:
		return
	_log_panel.global_position = Vector2(panel.global_position.x, panel.global_position.y + panel.size.y + 8.0)


func _render_growth_log() -> void:
	if _log_list == null:
		return
	for c in _log_list.get_children():
		c.queue_free()
	if _growth_log.is_empty():
		var empty_lbl := Label.new()
		empty_lbl.text = "暂无记录"
		empty_lbl.modulate = Color(0.7, 0.7, 0.7)
		_log_list.add_child(empty_lbl)
		return
	# 最新的记录放最上面
	for i in range(_growth_log.size() - 1, -1, -1):
		var e: Dictionary = _growth_log[i]
		var delta := int(e.get("delta", 0))
		var col := "#5fd68a" if delta > 0 else "#ff8a6a"
		var sign := "+" if delta > 0 else ""
		var lbl := RichTextLabel.new()
		lbl.bbcode_enabled = true
		lbl.fit_content = true
		lbl.scroll_active = false
		lbl.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
		lbl.text = "[color=#888]D%s %s[/color] %s [color=%s]%s%d[/color] [color=#b8b8b8]%s[/color]" % [
			e.get("day", 0), e.get("time", ""), e.get("name", ""), col, sign, delta, e.get("reason", ""),
		]
		_log_list.add_child(lbl)


## 记一笔增减；面板开着时立刻重绘，关着时下次打开自然是最新的。
func _push_growth_log(subject_name: String, delta: int, reason: String) -> void:
	_growth_log.append({
		"day": WorldClock.get_day(),
		"time": WorldClock.format_time(),
		"name": subject_name,
		"delta": delta,
		"reason": reason,
	})
	if _growth_log.size() > _GROWTH_LOG_MAX:
		_growth_log.pop_front()
	if _log_panel != null and _log_panel.visible:
		_render_growth_log()


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
	_show_banner(9.0)


## 镇务结果中央横幅（复用 day_banner）
func flash_mayor_toast(title: String, sub: String) -> void:
	if _day_banner == null:
		return
	var body := "[center][b]%s[/b]" % title
	if sub != "":
		body += "\n[color=#cdd6e6]%s[/color]" % sub
	body += "[/center]"
	_day_banner_label.text = body
	_show_banner(6.0)


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
	var prev_view := _last_view
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
	# 分数变动 → 在对应分数右侧飘字说明原因，并存进常驻记录日志
	if not had_prev:
		return
	var evs: Array = view.get("belief_events", [])
	var scores: Dictionary = view.get("scores", {})
	var dp := int(round(float(scores.get("player", 0.0)) - float(prev_scores.get("player", 0.0))))
	var do_ := int(round(float(scores.get(_opponent_id, 0.0)) - float(prev_scores.get(prev_opp, 0.0))))
	if dp != 0:
		var rp := _pending_action_reason("player")
		if rp == "":
			rp = _reason_text(evs, "player")
		if rp == "":
			rp = _infer_component_reason(prev_view, "player", view, "player")
		_float_delta(player_score_lbl, dp, rp)
		_push_growth_log("你", dp, rp)
	if do_ != 0:
		var ro := _pending_action_reason("opponent")
		if ro == "":
			ro = _reason_text(evs, _opponent_id)
		if ro == "":
			ro = _infer_component_reason(prev_view, prev_opp, view, _opponent_id)
		_float_delta(opponent_score_lbl, do_, ro)
		_push_growth_log(_id_to_name(_opponent_id), do_, ro)
	_pending_opponent_actions.clear()


## 把对手今天真实做过的动作（visit/promise 拉票、smear/poach 冲你）
## 直接翻成归因文案——是什么就是什么，不用去猜权重子项，也不会跟"对手落后
## 要追赶/领先要收手"的橡皮筋机制脱节（那部分逻辑在 opponent_ai.py，没动）。
func _pending_action_reason(target_side: String) -> String:
	var labels: Dictionary = (
		{"visit": "拜访拉票", "promise": "许诺拉票"} if target_side == "opponent"
		else {"smear": "抹黑你", "poach": "挖你墙脚"}
	)
	var parts: Array = []
	for a in _pending_opponent_actions:
		var t := String(a.get("action_type", ""))
		if not labels.has(t):
			continue
		var target_name := _id_to_name(String(a.get("target_npc", "")))
		parts.append("%s%s" % [target_name, labels[t]])
	if parts.is_empty():
		return ""
	var joined := ""
	for i in range(parts.size()):
		if i > 0:
			joined += "、"
		joined += parts[i]
	return "对手" + joined


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


## 把某候选人在所有 voter 上的子项权重（好感/承诺/辩论/事件/人情…）加总
func _breakdown_sum(view: Dictionary, candidate_id: String) -> Dictionary:
	var sums := {}
	for row in view.get("latest_weights", []):
		if not (row is Dictionary):
			continue
		if String(row.get("candidate_id", "")) != candidate_id:
			continue
		var bd = row.get("breakdown", {})
		if not (bd is Dictionary):
			continue
		for k in bd.keys():
			sums[k] = float(sums.get(k, 0.0)) + float(bd[k])
	return sums


## 没有具体谣言归因时的兜底：对比子项权重前后总和，挑变化幅度最大的一项说明原因
## （例如好感涨了/承诺兑现了/辩论出色/舆论事件/人情站队/执政包袱）
func _infer_component_reason(prev_view: Dictionary, prev_candidate_id: String, view: Dictionary, cur_candidate_id: String) -> String:
	var prev_sums := _breakdown_sum(prev_view, prev_candidate_id)
	var cur_sums := _breakdown_sum(view, cur_candidate_id)
	var keys := {}
	for k in prev_sums.keys():
		keys[k] = true
	for k in cur_sums.keys():
		keys[k] = true
	var best_key := ""
	var best_diff := 0.0
	for k in keys.keys():
		var d: float = float(cur_sums.get(k, 0.0)) - float(prev_sums.get(k, 0.0))
		if absf(d) > absf(best_diff):
			best_diff = d
			best_key = k
	if best_key == "" or absf(best_diff) < 0.3:
		return "综合变化"
	match best_key:
		"promise":
			return "履约" if best_diff > 0 else "违约"
		"debate":
			return "辩论出色" if best_diff > 0 else "辩论失分"
		"event":
			return "舆论向好" if best_diff > 0 else "舆论受挫"
		"incumbency":
			return "执政包袱减轻" if best_diff > 0 else "执政包袱加重"
		"loyalty":
			return "人情站队" if best_diff > 0 else "人情流失"
		"canvass":
			return "拉票承诺打动人心" if best_diff > 0 else "综合变化"
		_:
			return "好感上升" if best_diff > 0 else "好感下降"


## 分数右侧飘小字：上浮 + 淡出，不占布局空间
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
	# 飘字时长加倍，给玩家更多时间看清原因文案
	var tw := create_tween().set_parallel(true)
	tw.tween_property(lbl, "position:y", start.y - 22.0, 2.8).set_ease(Tween.EASE_OUT)
	tw.tween_property(lbl, "modulate:a", 0.0, 2.8).set_delay(0.7)
	tw.chain().tween_callback(lbl.queue_free)


func _on_opponent_action(info: Dictionary) -> void:
	# 实际气泡显示交给 main.gd._on_opponent_action（找 NPC 节点）
	# 这里只记一笔，供随后的 election_state 推送精确归因用
	var action_type := String(info.get("action_type", ""))
	if action_type == "":
		return
	_pending_opponent_actions.append({
		"action_type": action_type,
		"target_npc": String(info.get("target_npc", "")),
	})


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

	# Header 行数会随内容变化，刷新后重算面板高度
	_resize_panel()


## 让面板高度随可见内容自适应，消除下方留白 / 内容出框。
## Header 是 fit_content 的 RichTextLabel，双行或追加权力提示时高度会变，
## 固定 panel_height 撑不住，必须按实际内容重算。
func _resize_panel() -> void:
	if not is_inside_tree():
		return
	if Engine.is_editor_hint():
		return          # 编辑器内不做异步重算，避免 @tool 下等帧异常
	var p := get_node_or_null("Panel") as Panel
	if p == null:
		return
	var vb := p.get_node_or_null("VBox") as VBoxContainer
	if vb == null:
		return
	# 等两帧：让容器完成布局、RichTextLabel 按宽度算好 fit_content 高度
	await get_tree().process_frame
	await get_tree().process_frame
	if not is_inside_tree():
		return
	var sep: float = float(vb.get_theme_constant("separation"))
	var h := 0.0
	var vis := 0
	for c in vb.get_children():
		if c is Control and (c as Control).visible:
			h += (c as Control).get_combined_minimum_size().y
			vis += 1
	if vis > 1:
		h += sep * (vis - 1)
	p.size.y = max(h + pad_top + pad_bottom, panel_height)


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
	# 这里只给最小高；真实高度由 _resize_panel 按内容重算
	p.offset_bottom = panel_margin_top + panel_height
	_resize_panel()

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
		"gossip": return "🗣 八卦日"
		"governance": return "📋 镇务日"
		"debate": return "🎤 辩论日"
		"crisis": return "⚡ 危机日"
		"vote": return "🗳 投票日"
		_: return "🌿 竞选日"


func _gen_hint(_day_idx: int, p: float, o: float) -> String:
	if _day_theme == "debate":
		return "[color=#ffd27f]💬 今天是辩论日，参加广场辩论——今晚 20:00 开票。[/color]"
	if _day_theme == "gossip":
		return "[color=#ffd27f]🗣 今天造谣采信率更高，抓紧时机为辩论铺路。[/color]"
	if _day_theme == "governance":
		return "[color=#ffd27f]📋 今天是镇务日，指派镇民干活、用政绩说话。[/color]"
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
	await get_tree().create_timer(0.6).timeout

	var rounds: Array = info.get("rounds", [])
	if rounds.is_empty():
		# 后端没给分轮数据（旧版本）→ 退回一次性揭晓
		await _tween_votes_to(p_target, o_target, 1.4)
	else:
		await _play_rounds(rounds, settled_opponent)

	# 胜负已分，唱票卡片收起——接下来交给 main.gd 的换届黑幕
	# （当选/离镇/迁入统一在那边一次性交代，不再在这里单开一块横幅）
	result_status.text = "[center][color=#888]胜负已分[/color][/center]"
	await get_tree().create_timer(0.5).timeout
	await _on_overlay_close()
	term_settled.emit(info, op_id)


## 逐轮唱票：每轮公布本轮投给谁，再把累计票数滚上去，最后一轮定胜负
func _play_rounds(rounds: Array, settled_opponent: String) -> void:
	for r in rounds:
		var rd: Dictionary = r
		var idx := int(rd.get("round", 0))
		var total := int(rd.get("total_rounds", rounds.size()))
		var is_final: bool = bool(rd.get("is_final", false))

		# 本轮是谁投的、投给了谁、为什么
		var names: Array[String] = []
		for b in rd.get("ballots", []):
			var who := _id_to_name(String((b as Dictionary).get("voter", "")))
			var to_id := String((b as Dictionary).get("voted_for", ""))
			var to_name: String = "你" if to_id == "player" else _id_to_name(to_id)
			var reason := String((b as Dictionary).get("reason", ""))
			if reason != "":
				names.append("%s→%s（%s）" % [who, to_name, reason])
			else:
				names.append("%s→%s" % [who, to_name])

		if is_final:
			result_status.text = "[center][color=#ffce8a]最终轮 %d/%d ：%s[/color][/center]" % [
				idx, total, "、".join(names)
			]
		else:
			result_status.text = "[center][color=#9fd4ff]第 %d/%d 轮开票：%s[/color][/center]" % [
				idx, total, "、".join(names)
			]

		var cum: Dictionary = rd.get("cumulative", {})
		await _tween_votes_to(
			int(cum.get("player", 0)),
			int(cum.get(settled_opponent, 0)),
			0.8
		)
		# 轮间留白，让玩家看清这一轮的变化
		if not is_final:
			await get_tree().create_timer(0.9).timeout


## 把两条票数条滚动到目标值，返回时动画已结束
func _tween_votes_to(p: int, o: int, dur: float) -> void:
	var p_from := pvote_bar.value
	var o_from := ovote_bar.value
	var tw := create_tween().set_parallel(true)
	tw.tween_property(pvote_bar, "value", float(p), dur).set_trans(Tween.TRANS_CUBIC)
	tw.tween_method(_set_player_num, p_from, float(p), dur).set_trans(Tween.TRANS_CUBIC)
	tw.tween_property(ovote_bar, "value", float(o), dur).set_trans(Tween.TRANS_CUBIC)
	tw.tween_method(_set_op_num, o_from, float(o), dur).set_trans(Tween.TRANS_CUBIC)
	await tw.finished


func _set_player_num(v: float) -> void:
	if pvote_num:
		pvote_num.text = "%d" % int(round(v))


func _set_op_num(v: float) -> void:
	if ovote_num:
		ovote_num.text = "%d" % int(round(v))


func _on_overlay_close() -> void:
	if not is_instance_valid(result_overlay):
		return
	var tw := create_tween()
	tw.tween_property(result_overlay, "modulate:a", 0.0, 0.3)
	await tw.finished
	if is_instance_valid(result_overlay):
		result_overlay.visible = false
		result_overlay.modulate.a = 1.0
