extends Node
## 全局游戏时钟（autoload）
##
## 管理游戏内时间，按固定间隔发出 tick 信号供 NPC 决策。
## 1 现实秒 = time_scale 游戏秒。

signal tick(game_time_str: String, total_minutes: int)
signal hour_changed(hour: int)

## 1 实秒 = 多少游戏秒。96 → 1 实秒 = 1.6 游戏分钟，单个游戏日=15 现实分钟。
## （竞选任期 3 天 → 单局约 45 分钟内决出胜负）
@export var time_scale: float = 96.0
## 起始小时（0-23）。从 6 点开始（早于 7 点的当日提示阈值），
## 保证游戏一开局就能自然跨过 7 点这个整点触发当日主题提示。
@export var start_hour: int = 6
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


## 前进指定分钟数（可跨小时/跨天）。玩家睡觉等"消耗时间"场景用；
## 照常发 tick / hour_changed，保证 NPC 日程与后端 time_tick 同步推进。
func advance_by_minutes(minutes: int) -> void:
	if minutes <= 0:
		return
	_total_seconds += float(minutes) * 60.0
	var current_minute: int = int(_total_seconds / 60.0)
	var current_hour: int = (current_minute / 60) % 24
	_last_tick_minute = current_minute
	tick.emit(format_time(), current_minute)
	if current_hour != _last_hour:
		_last_hour = current_hour
		hour_changed.emit(current_hour)


## 直接跳到次日指定小时（默认复用 start_hour，即天亮早晨）。
## 换届黑幕过渡用：整晚不用真的挂机等，大家都去睡了，直接跳到第二天。
func skip_to_next_morning(hour: int = -1) -> void:
	var h: int = hour if hour >= 0 else start_hour
	var next_day: int = get_day() + 1
	_total_seconds = float(next_day * 24 * 60 + h * 60) * 60.0
	var current_minute: int = int(_total_seconds / 60.0)
	_last_tick_minute = current_minute
	_last_hour = h
	tick.emit(format_time(), current_minute)
	hour_changed.emit(h)
