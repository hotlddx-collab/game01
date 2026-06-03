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


func set_quest(qid: String, title: String, desc: String, kind: String, target_npc: String, message_summary: String) -> void:
	_current_qid = qid
	title_label.text = "📜 [b]%s[/b]" % title
	var detail_lines: Array[String] = []
	if desc != "":
		detail_lines.append("[color=#5a4a3a]%s[/color]" % desc)
	match kind:
		"deliver":
			if target_npc != "":
				detail_lines.append("→ 把物品送到 [b]%s[/b]" % _label_for(target_npc))
		"relay":
			if message_summary != "":
				detail_lines.append("💬 [i]%s[/i]" % message_summary)
			if target_npc != "":
				detail_lines.append("→ 找 [b]%s[/b] 说出来" % _label_for(target_npc))
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
