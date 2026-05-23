extends Node
## 昼夜色调切换
##
## 监听 WorldClock，根据时间平滑调节 CanvasModulate 颜色。
## 时段（24h 制）：
##   05:00-07:00 黎明：暖橙
##   07:00-17:00 白天：纯白（无滤镜）
##   17:00-19:00 黄昏：橙红
##   19:00-22:00 夜晚：紫蓝
##   22:00-05:00 深夜：深蓝

@export_node_path("CanvasModulate") var canvas_modulate_path: NodePath
@export var lerp_speed: float = 1.5  # 颜色过渡速度

# 关键节点：(小时, 颜色)
const COLOR_KEYS: Array = [
	[0.0,  Color(0.30, 0.35, 0.55)],  # 深夜
	[5.0,  Color(0.55, 0.50, 0.55)],  # 黎明前
	[7.0,  Color(1.00, 0.92, 0.85)],  # 清晨
	[9.0,  Color(1.00, 1.00, 1.00)],  # 白天
	[16.0, Color(1.00, 1.00, 1.00)],  # 白天
	[18.0, Color(1.00, 0.78, 0.55)],  # 黄昏
	[19.5, Color(0.85, 0.55, 0.55)],  # 黄昏后
	[21.0, Color(0.45, 0.45, 0.65)],  # 夜晚
	[23.0, Color(0.30, 0.35, 0.55)],  # 深夜
]

var _modulate_node: CanvasModulate
var _target_color: Color = Color.WHITE


func _ready() -> void:
	if canvas_modulate_path == NodePath(""):
		push_warning("DayNight: 未设置 canvas_modulate_path")
		return
	_modulate_node = get_node_or_null(canvas_modulate_path) as CanvasModulate
	if _modulate_node == null:
		push_warning("DayNight: 找不到 CanvasModulate 节点 '%s'" % canvas_modulate_path)
		return
	_target_color = _color_for_hour(WorldClock.get_hour())
	_modulate_node.color = _target_color
	WorldClock.tick.connect(_on_tick)


func _process(delta: float) -> void:
	if _modulate_node == null:
		return
	_modulate_node.color = _modulate_node.color.lerp(_target_color, clampf(delta * lerp_speed, 0.0, 1.0))


func _on_tick(_time_str: String, total_minutes: int) -> void:
	# 用浮点小时算精确插值
	var minutes_today: int = total_minutes % (24 * 60)
	var hour_f: float = float(minutes_today) / 60.0
	_target_color = _color_for_hour(hour_f)


func _color_for_hour(h: float) -> Color:
	# 在 COLOR_KEYS 区间线性插值
	var n: int = COLOR_KEYS.size()
	for i in range(n - 1):
		var ha: float = COLOR_KEYS[i][0]
		var hb: float = COLOR_KEYS[i + 1][0]
		if h >= ha and h < hb:
			var t: float = (h - ha) / max(hb - ha, 0.0001)
			return (COLOR_KEYS[i][1] as Color).lerp(COLOR_KEYS[i + 1][1] as Color, t)
	# 边界
	return COLOR_KEYS[n - 1][1] as Color
