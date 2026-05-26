extends Node
## 全局游戏时钟（autoload）
##
## 管理游戏内时间，按固定间隔发出 tick 信号供 NPC 决策。
## 1 现实秒 = time_scale 游戏秒。

signal tick(game_time_str: String, total_minutes: int)
signal hour_changed(hour: int)

## 1 实秒 = 多少游戏秒。默认 60 → 1 实秒 = 1 游戏分钟。
@export var time_scale: float = 60.0
## 起始小时（0-23）。
@export var start_hour: int = 8
## 多少游戏分钟发一次 tick。
@export var tick_interval_minutes: int = 10

var _total_seconds: float = 0.0
var _last_tick_minute: int = -1
var _last_hour: int = -1


func _ready() -> void:
	_total_seconds = float(start_hour) * 3600.0
	_last_hour = start_hour


func _process(delta: float) -> void:
	_total_seconds += delta * time_scale
	var current_minute: int = int(_total_seconds / 60.0)
	var current_hour: int = (current_minute / 60) % 24

	# 每 tick_interval_minutes 发一次 tick
	if current_minute / tick_interval_minutes != _last_tick_minute / tick_interval_minutes:
		_last_tick_minute = current_minute
		tick.emit(format_time(), current_minute)

	if current_hour != _last_hour:
		_last_hour = current_hour
		hour_changed.emit(current_hour)


## 当前游戏时间字符串 "HH:MM"
func format_time() -> String:
	var total_minutes: int = int(_total_seconds / 60.0)
	var h: int = (total_minutes / 60) % 24
	var m: int = total_minutes % 60
	return "%02d:%02d" % [h, m]


## 当前小时（0-23）
func get_hour() -> int:
	return (int(_total_seconds / 60.0) / 60) % 24


## 当前总游戏分钟数（自起始累计）
func get_total_minutes() -> int:
	return int(_total_seconds / 60.0)


## 当前游戏日序号（自启动累计，0,1,2...）
func get_day() -> int:
	return int(_total_seconds / 60.0) / (24 * 60)
