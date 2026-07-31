extends CanvasLayer
## 辩论日面板（D6 辩论阶段自动开启 / 调试 Ctrl+B 手动触发）
##
## 流程：
##   debate_start → 收到 3 道辩题 → 逐题展示，玩家从 4 个立场象限中选 1
##   选完一题 → 请求对手即时反驳 → 显示反驳 → 下一题
##   3 题答完 → debate_submit → 显示评分结果 → 关闭
##
## 立场象限固定顺序：radical / conservative / pleasing / pragmatic

const STANCE_ORDER := ["radical", "conservative", "pleasing", "pragmatic"]
const STANCE_ICON := {
	"radical": "🔥",
	"conservative": "🛡",
	"pleasing": "💖",
	"pragmatic": "🧭",
}

@onready var backdrop: ColorRect = $Backdrop
@onready var title: RichTextLabel = %Title
@onready var asker_label: RichTextLabel = %AskerLabel
@onready var question_label: RichTextLabel = %QuestionLabel
@onready var options_vbox: VBoxContainer = %OptionsVBox
@onready var rebuttal_box: PanelContainer = %RebuttalBox
@onready var rebuttal_label: RichTextLabel = %RebuttalLabel
@onready var next_button: Button = %NextButton
@onready var result_label: RichTextLabel = %ResultLabel
@onready var close_button: Button = %CloseButton

var _questions: Array = []
var _stance_labels: Dictionary = {}
var _opponent_id: String = ""
var _index: int = 0
var _answers: Dictionary = {}          # {index(int): stance(String)}
var _done_terms: Dictionary = {}       # 已辩论过的 term_id，避免重复弹
var _done_sessions: Dictionary = {}    # 已开过的场次 "term:session"，避免重开
var _pending_session: int = 0          # 本次请求的场次
var _total_sessions: int = 3
var _current_term_id: int = -1
var _waiting_rebut: bool = false


func _ready() -> void:
	backdrop.visible = false
	rebuttal_box.visible = false
	result_label.visible = false
	close_button.visible = false
	next_button.pressed.connect(_on_next_pressed)
	close_button.pressed.connect(_close)

	if AgentClient.has_signal("debate_questions_received"):
		AgentClient.debate_questions_received.connect(_on_questions)
	if AgentClient.has_signal("debate_rebuttal_received"):
		AgentClient.debate_rebuttal_received.connect(_on_rebuttal)
	if AgentClient.has_signal("debate_result_received"):
		AgentClient.debate_result_received.connect(_on_result)
	if AgentClient.has_signal("election_state_received"):
		AgentClient.election_state_received.connect(_on_election_state)


func _input(event: InputEvent) -> void:
	if _typing_in_textbox():
		return
	# 调试：Ctrl+B 手动触发辩论
	if event is InputEventKey and event.pressed and not event.echo:
		if event.keycode == KEY_B and event.ctrl_pressed:
			print("[debate] DEBUG: 手动触发辩论")
			AgentClient.request_debate_start()
			get_viewport().set_input_as_handled()


func _typing_in_textbox() -> bool:
	var f := get_viewport().gui_get_focus_owner()
	return f is LineEdit or f is TextEdit


## 辩论日分多场：上午/下午/傍晚各一场，到点才开，避免整天只辩一次太单薄
const DEBATE_SESSION_HOURS := [9, 14, 18]
const DEBATE_CLOSE_HOUR := 21


func _on_election_state(view: Dictionary) -> void:
	## 辩论日（D2）→ 到场次时刻自动拉辩题。
	var phase := String(view.get("phase", ""))
	var tid := int(view.get("term_id", -1))
	if phase != "debate":
		return
	if backdrop.visible:
		return
	var h := WorldClock.get_hour()
	if h >= DEBATE_CLOSE_HOUR:
		return
	# 按当前时刻算「该开第几场」，已开过的场次不再重开
	var due := -1
	for i in DEBATE_SESSION_HOURS.size():
		if h >= int(DEBATE_SESSION_HOURS[i]):
			due = i
	if due < 0:
		return                      # 天还没亮到第一场，不打扰
	var key := "%d:%d" % [tid, due]
	if _done_sessions.has(key):
		return
	_pending_session = due
	AgentClient.request_debate_start(due)


func _on_questions(info: Dictionary) -> void:
	if bool(info.get("already_done", false)):
		_done_terms[int(info.get("term_id", -1))] = true
		return
	var qs: Array = info.get("questions", [])
	if qs.is_empty():
		return
	_questions = qs
	_stance_labels = info.get("stance_labels", {})
	_opponent_id = String(info.get("opponent_id", ""))
	_current_term_id = int(info.get("term_id", -1))
	_pending_session = int(info.get("session", _pending_session))
	_total_sessions = int(info.get("total_sessions", _total_sessions))
	_index = 0
	_answers = {}
	_open()
	_show_question()


func _open() -> void:
	backdrop.visible = true
	title.text = "[center][b]🎤 镇长辩论会  第 %d/%d 场[/b][/center]" % [
		_pending_session + 1, _total_sessions,
	]


func _close() -> void:
	backdrop.visible = false
	# 记下本场已开，同场次不再重弹；后续场次到点仍会开
	if _current_term_id >= 0:
		_done_sessions["%d:%d" % [_current_term_id, _pending_session]] = true
		if _pending_session + 1 >= _total_sessions:
			_done_terms[_current_term_id] = true


func _show_question() -> void:
	rebuttal_box.visible = false
	result_label.visible = false
	close_button.visible = false
	next_button.visible = false

	var q: Dictionary = _questions[_index]
	var asker := String(q.get("asker_name", "?"))
	asker_label.text = "[center][color=#ffd479]%s 提问  (%d/%d)[/color][/center]" % [
		asker, _index + 1, _questions.size()
	]
	question_label.text = "[center]%s[/center]" % String(q.get("q", ""))

	# 清空旧选项
	for c in options_vbox.get_children():
		c.queue_free()

	var options: Dictionary = q.get("options", {})
	for stance in STANCE_ORDER:
		if not options.has(stance):
			continue
		var btn := Button.new()
		btn.text = "%s %s" % [STANCE_ICON.get(stance, ""), String(options[stance])]
		btn.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
		btn.custom_minimum_size = Vector2(560, 44)
		btn.add_theme_font_size_override("font_size", 13)
		btn.set_meta("stance", stance)
		btn.set_meta("opt_text", String(options[stance]))
		btn.pressed.connect(_on_option_pressed.bind(btn))
		options_vbox.add_child(btn)


func _on_option_pressed(btn: Button) -> void:
	if _waiting_rebut:
		return
	var stance := String(btn.get_meta("stance", ""))
	var opt_text := String(btn.get_meta("opt_text", ""))
	if stance == "":
		return
	_answers[_index] = stance

	# 禁用所有选项，高亮所选
	for c in options_vbox.get_children():
		if c is Button:
			c.disabled = true
			if c == btn:
				c.modulate = Color(0.7, 1.0, 0.7)

	# 请求对手反驳
	_waiting_rebut = true
	rebuttal_box.visible = true
	rebuttal_label.text = "[color=#aaa]对手正在反驳……[/color]"
	next_button.visible = false
	var q: Dictionary = _questions[_index]
	AgentClient.request_debate_rebut(_index, String(q.get("q", "")), stance, opt_text)


func _on_rebuttal(info: Dictionary) -> void:
	if not backdrop.visible:
		return
	if int(info.get("question_index", -1)) != _index:
		return
	_waiting_rebut = false
	var op_name := _id_to_name(_opponent_id)
	rebuttal_label.text = "[color=#ff8888]💢 %s 反驳：[/color]%s" % [
		op_name, String(info.get("text", ""))
	]
	if _index + 1 < _questions.size():
		next_button.text = "下一题 ▶"
	else:
		next_button.text = "提交辩论 ✔"
	next_button.visible = true


func _on_next_pressed() -> void:
	if _waiting_rebut:
		return
	if _index + 1 < _questions.size():
		_index += 1
		_show_question()
	else:
		_submit()


func _submit() -> void:
	# 键转字符串发后端（JSON 对象键为字符串）
	var payload: Dictionary = {}
	for k in _answers.keys():
		payload[str(k)] = _answers[k]
	AgentClient.request_debate_submit(payload)
	next_button.visible = false
	rebuttal_box.visible = false
	asker_label.text = "[center][color=#ffd479]辩论结束，正在统计反响……[/color][/center]"
	question_label.text = ""
	for c in options_vbox.get_children():
		c.queue_free()


func _on_result(info: Dictionary) -> void:
	if not backdrop.visible:
		return
	if _current_term_id >= 0:
		_done_terms[_current_term_id] = true

	var p_total := float(info.get("player_debate_total", 0.0))
	var o_total := float(info.get("opponent_debate_total", 0.0))
	var labels: Dictionary = info.get("stance_labels", _stance_labels)

	var lines: Array[String] = []
	lines.append("[center][b]🗳 辩论反响[/b][/center]")
	lines.append("")
	# 你的立场倾向（统计本场选择）
	var stance_count: Dictionary = {}
	for k in _answers.keys():
		var s := String(_answers[k])
		stance_count[s] = int(stance_count.get(s, 0)) + 1
	var stance_summary: Array[String] = []
	for s in STANCE_ORDER:
		if stance_count.has(s):
			stance_summary.append("%s×%d" % [String(labels.get(s, s)), int(stance_count[s])])
	lines.append("你的立场：%s" % (" · ".join(stance_summary) if stance_summary.size() > 0 else "—"))
	lines.append("")
	# 声望影响对比
	var p_color := "#88ee88" if p_total >= o_total else "#ff8888"
	lines.append("本场为你带来声望：[color=%s][b]%+.1f[/b][/color]" % [p_color, p_total])
	lines.append("对手获得声望：[color=#ffcc66]%+.1f[/color]" % o_total)
	lines.append("")
	if p_total > o_total:
		lines.append("[center][color=#88ee88]你的立场比对手更得人心 ✔[/color][/center]")
	elif p_total < o_total:
		lines.append("[center][color=#ff8888]对手的立场更受欢迎，需努力 ✘[/color][/center]")
	else:
		lines.append("[center][color=#ffd479]势均力敌[/color][/center]")

	result_label.text = "\n".join(lines)
	result_label.visible = true
	close_button.visible = true
	asker_label.text = ""
	question_label.text = ""


func _id_to_name(npc_id: String) -> String:
	if npc_id == "" or npc_id == "player":
		return npc_id
	for n in get_tree().get_nodes_in_group("npc"):
		if "animal_id" in n and n.animal_id == npc_id:
			return n.animal_name if "animal_name" in n else npc_id
	return npc_id
