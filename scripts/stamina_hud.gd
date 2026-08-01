extends CanvasLayer
## 体力条 HUD
##
## 一条槽两种状态：
##   普通态 —— 蓝条「⚡ 体力」，按游戏日线性流逝，低于疲劳线转橙红提示「该睡了」。
##   冲刺态 —— 睡醒后同一条槽变黄「💨 冲刺」，倒着掉，期间移动加速；掉完自动切回蓝条。

const COLOR_NORMAL := Color(0.42, 0.78, 0.9, 1)
const COLOR_TIRED := Color(0.9, 0.45, 0.32, 1)
const COLOR_SPRINT := Color(1, 0.82, 0.35, 1)

@onready var name_label: Label = %Name
@onready var bar: ProgressBar = %Bar
@onready var state_label: Label = %State

var _fill: StyleBoxFlat = null


func _ready() -> void:
	# 复制一份填充样式，避免改色污染场景里共享的 StyleBox 资源
	var src := bar.get_theme_stylebox("fill") as StyleBoxFlat
	if src != null:
		_fill = src.duplicate() as StyleBoxFlat
		bar.add_theme_stylebox_override("fill", _fill)
	var player := get_tree().get_first_node_in_group("player")
	if player != null and player.has_signal("stamina_changed"):
		player.stamina_changed.connect(_on_stamina_changed)


func _on_stamina_changed(cur: float, max_value: float, tired: bool, sprinting: bool) -> void:
	bar.max_value = max_value
	bar.value = cur
	if sprinting:
		_set_fill(COLOR_SPRINT)
		name_label.text = "💨 冲刺"
		name_label.add_theme_color_override("font_color", COLOR_SPRINT)
		state_label.text = "加速中"
		state_label.add_theme_color_override("font_color", COLOR_SPRINT)
		return
	name_label.text = "⚡ 体力"
	name_label.add_theme_color_override("font_color", Color(1, 1, 1, 1))
	if tired:
		_set_fill(COLOR_TIRED)
		state_label.text = "😮‍💨 该睡了"
		state_label.add_theme_color_override("font_color", COLOR_TIRED)
	else:
		_set_fill(COLOR_NORMAL)
		state_label.text = ""


func _set_fill(c: Color) -> void:
	if _fill != null:
		_fill.bg_color = c
