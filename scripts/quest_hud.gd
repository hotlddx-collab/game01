extends CanvasLayer
## 任务 HUD - 屏幕左上角持续显示当前任务
##
## 接受任务时由 main.gd 调用 set_quest()；完成时调用 clear_quest()。

@onready var panel: Panel = $Panel
@onready var title_label: RichTextLabel = $Panel/VBox/Title
@onready var detail_label: RichTextLabel = $Panel/VBox/Detail

var _current_qid: String = ""


func _ready() -> void:
	panel.visible = false


func set_quest(qid: String, title: String, desc: String, kind: String, target_npc: String, message_summary: String, collect_item: String = "", required: int = 0, progress: int = 0) -> void:
	_current_qid = qid
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
	detail_label.text = "\n".join(detail_lines)
	panel.visible = true


func clear_quest() -> void:
	_current_qid = ""
	panel.visible = false


func _label_for(animal_id: String) -> String:
	# 根据 ID 反查 NPC 中文名
	for n in get_tree().get_nodes_in_group("npc"):
		if "animal_id" in n and n.animal_id == animal_id:
			return n.animal_name if "animal_name" in n else animal_id
	return animal_id
