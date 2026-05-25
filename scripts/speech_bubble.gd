class_name SpeechBubble
extends Node2D
## 头顶气泡（NPC 说话时显示）。
##
## 自适应宽高：根据文字长短自动计算面板尺寸 + 居中。
## 包含 3 部分：
##   - 说话者名字（小字蓝色，顶部）
##   - 文字内容（黑色主体）
##   - 箭头（指向说话者）

@export var max_body_width: float = 220.0
@export var fade_in_time: float = 0.15
@export var fade_out_time: float = 0.3
@export var bottom_gap: float = 6.0  # 气泡底到角色头顶的间距

@onready var _panel: PanelContainer = %Panel
@onready var _speaker_label: Label = %SpeakerLabel
@onready var _body_label: Label = %BodyLabel
@onready var _arrow: Polygon2D = %Arrow


func show_text(speaker: String, text: String, lifetime: float = 4.0) -> void:
	_speaker_label.text = speaker
	_body_label.text = text
	# 限制文字最大宽度（autowrap 会换行）
	_body_label.custom_minimum_size = Vector2(min(text.length() * 14, max_body_width), 0)

	modulate.a = 0.0
	# 等一帧让 layout 重算 panel.size
	await get_tree().process_frame
	_layout_centered()

	var tween_in := create_tween()
	tween_in.tween_property(self, "modulate:a", 1.0, fade_in_time)

	await get_tree().create_timer(lifetime).timeout

	var tween_out := create_tween()
	tween_out.tween_property(self, "modulate:a", 0.0, fade_out_time)
	await tween_out.finished
	queue_free()


## 把 panel 居中到 Node2D 原点（角色头顶 0,0 应在角色头上方）+ 箭头放底部
func _layout_centered() -> void:
	var s: Vector2 = _panel.size
	# Panel 顶点位置：左移半宽居中、向上挪整个高度 + bottom_gap 留给箭头
	_panel.position = Vector2(-s.x * 0.5, -s.y - bottom_gap)
	# 箭头：紧贴 Panel 底部中央
	_arrow.position = Vector2(0, -bottom_gap)
