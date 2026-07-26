extends CanvasLayer
## 任务 HUD - 屏幕左上角持续显示当前任务
##
## 接受任务时由 main.gd 调用 set_quest()；完成时调用 clear_quest()。
## 镇务任务（现任镇长专属）显示在 NPC 任务下方。

@onready var panel: Panel = $Panel
@onready var title_label: RichTextLabel = $Panel/VBox/Title
@onready var detail_label: RichTextLabel = $Panel/VBox/Detail
@onready var _vbox: VBoxContainer = $Panel/VBox

var _current_qid: String = ""
var _has_quest: bool = false
var _has_mayor: bool = false
var _mayor_view: Dictionary = {}   # 当前镇务视图（供“处理中/结果”阶段复用标题）

var _mayor_label: RichTextLabel


func _ready() -> void:
	panel.visible = false
	_build_mayor_label()
	if AgentClient.has_signal("mayor_task_state_received"):
		AgentClient.mayor_task_state_received.connect(_on_mayor_task_state)
	call_deferred("_initial_mayor_query")


func _initial_mayor_query() -> void:
	await get_tree().create_timer(1.2).timeout
	if AgentClient.is_connected_to_server() and AgentClient.has_method("request_mayor_task_query"):
		AgentClient.request_mayor_task_query()


func _on_mayor_task_state(info: Dictionary) -> void:
	if info.get("active", false):
		set_mayor_task(info.get("task", {}))
	else:
		clear_mayor_task()


func _build_mayor_label() -> void:
	_mayor_label = RichTextLabel.new()
	_mayor_label.bbcode_enabled = true
	_mayor_label.fit_content = true
	_mayor_label.scroll_active = false
	_mayor_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_mayor_label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_mayor_label.visible = false
	_vbox.add_child(_mayor_label)


func set_quest(qid: String, title: String, desc: String, kind: String, target_npc: String, message_summary: String, collect_item: String = "", required: int = 0, progress: int = 0) -> void:
	_current_qid = qid
	_has_quest = true
	var title_text := "[color=#3a2a18]📜 [b]%s[/b][/color]" % title
	if kind == "collect" and required > 0:
		title_text += "  [color=#7a5c3a](%d/%d)[/color]" % [progress, required]
	title_label.text = title_text
	var detail_lines: Array[String] = []
	if desc != "":
		detail_lines.append("[color=#5a4a3a]%s[/color]" % desc)
	match kind:
		"collect":
			if collect_item != "":
				var nm := ItemDB.get_item_name(collect_item)
				detail_lines.append("[color=#4a3a28]→ 收集 [b]%s[/b]，回去找委托人[/color]" % nm)
		"deliver":
			if target_npc != "":
				detail_lines.append("[color=#4a3a28]→ 把物品送到 [b]%s[/b][/color]" % _label_for(target_npc))
		"relay":
			if message_summary != "":
				detail_lines.append("[color=#6a5236]💬 [i]%s[/i][/color]" % message_summary)
			if target_npc != "":
				detail_lines.append("[color=#4a3a28]→ 找 [b]%s[/b] 说出来[/color]" % _label_for(target_npc))
	title_label.visible = true
	detail_label.visible = true
	detail_label.text = "\n".join(detail_lines)
	panel.visible = true
	_resize_panel()


func clear_quest() -> void:
	_current_qid = ""
	_has_quest = false
	title_label.visible = false
	detail_label.visible = false
	_refresh_visibility()


## 镇务任务：info 为 view()（含 title/hint/target_name/kind），无则清空
func set_mayor_task(info: Dictionary) -> void:
	if info.is_empty():
		clear_mayor_task()
		return
	_has_mayor = true
	_mayor_view = info
	var title: String = info.get("title", "镇务")
	var hint: String = info.get("hint", "")
	var target_name: String = info.get("target_name", "")
	var txt := "[color=#7a3a18]🏛 [b]镇务：%s[/b][/color]" % title
	if target_name != "":
		txt += "\n[color=#8a4a2a]对象：%s[/color]" % target_name
	if hint != "":
		txt += "\n[color=#6a5236][i]%s[/i][/color]" % hint
	txt += "\n[color=#4a3a28]→ 找一位合适的居民，对话中「安排」TA 去做[/color]"
	_mayor_label.text = txt
	_mayor_label.visible = true
	panel.visible = true
	_resize_panel()


func clear_mayor_task() -> void:
	_has_mayor = false
	_mayor_view = {}
	if _mayor_label != null:
		_mayor_label.visible = false
	_refresh_visibility()


## 已安排某居民执行中 → HUD 改显“处理中”，不清空追踪
func set_mayor_in_progress(executor_name: String) -> void:
	if _mayor_view.is_empty():
		return
	_has_mayor = true
	var title: String = _mayor_view.get("title", "镇务")
	var txt := "[color=#7a3a18]🏛 [b]镇务：%s[/b][/color]" % title
	txt += "\n[color=#8a6a2a]⏳ 已安排 [b]%s[/b] 处理中…[/color]" % executor_name
	_mayor_label.text = txt
	_mayor_label.visible = true
	panel.visible = true
	_resize_panel()


## 结果反馈 → HUD 显示成败 + 声望变化，数秒后自动清空
func set_mayor_result(executor_name: String, outcome: String, injured: bool = false, score_before: int = -1, score_after: int = -1) -> void:
	if _mayor_view.is_empty():
		return
	var title: String = _mayor_view.get("title", "镇务")
	var badge := "⚠️ 勉强了事"
	var col := "#8a6a2a"
	if outcome == "great":
		badge = "✅ 圆满解决"
		col = "#2a6a2a"
	elif outcome == "botch":
		badge = "❌ 闹出麻烦"
		col = "#a03020"
	if injured:
		badge += "（%s受伤）" % executor_name
	var txt := "[color=#7a3a18]🏛 [b]镇务：%s[/b][/color]" % title
	txt += "\n[color=%s]%s[/color]" % [col, badge]
	if score_before >= 0 and score_after >= 0:
		var d := score_after - score_before
		if d > 0:
			txt += "\n[color=#2a6a2a]声望 %d→%d (+%d)[/color]" % [score_before, score_after, d]
		elif d < 0:
			txt += "\n[color=#a03020]声望 %d→%d (%d)[/color]" % [score_before, score_after, d]
		else:
			txt += "\n[color=#8a6a2a]声望无明显变化 (%d)[/color]" % score_after
	_mayor_label.text = txt
	_mayor_label.visible = true
	panel.visible = true
	_resize_panel()
	await get_tree().create_timer(4.0).timeout
	clear_mayor_task()


func _refresh_visibility() -> void:
	panel.visible = _has_quest or _has_mayor
	if panel.visible:
		_resize_panel()


## 让面板高度随可见内容自适应（避免文字冒出固定面板）
func _resize_panel() -> void:
	# 等一帧让容器布局 + RichTextLabel 按宽度算好 fit_content 高度
	await get_tree().process_frame
	await get_tree().process_frame
	var sep := _vbox.get_theme_constant("separation")
	var h := 0.0
	var vis := 0
	for c in _vbox.get_children():
		if c is Control and (c as Control).visible:
			h += (c as Control).get_combined_minimum_size().y
			vis += 1
	if vis > 1:
		h += sep * (vis - 1)
	# VBox 上下内边距各 10
	panel.size.y = h + 20.0


func _label_for(animal_id: String) -> String:
	# 根据 ID 反查 NPC 中文名
	for n in get_tree().get_nodes_in_group("npc"):
		if "animal_id" in n and n.animal_id == animal_id:
			return n.animal_name if "animal_name" in n else animal_id
	return animal_id
