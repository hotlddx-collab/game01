extends CanvasLayer
## 屏幕右上角时间 HUD

@onready var time_label: Label = %TimeLabel


func _ready() -> void:
	WorldClock.tick.connect(_on_tick)
	_refresh()


func _on_tick(_time_str: String, _total_minutes: int) -> void:
	_refresh()


func _refresh() -> void:
	if time_label:
		time_label.text = WorldClock.format_time()
