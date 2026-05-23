extends CharacterBody2D
class_name Animal
## 动物 NPC
##
## 加载 persona JSON（性格 + 日程），按 WorldClock 时间走向目标地点。
## 头顶气泡显示当前 intent。可被玩家用 E 触发对话。
##
## P0 用直线 move_toward 移动；P1+ 换 NavigationAgent2D。

@export_file("*.json") var persona_file: String = ""
@export var move_speed: float = 80.0
@export var arrive_distance: float = 6.0

var animal_id: String = ""
var animal_name: String = ""
var personality: String = ""
var speech_style: String = ""
var catchphrase: String = ""
var color_hex: String = "#a06b3c"

var _schedule: Array = []          # [{time, location, intent}]
var _current_intent: String = "..."
var _target_location: String = ""
var _target_pos: Vector2 = Vector2.ZERO
var _moving: bool = false

@onready var sprite: ColorRect = %Body
@onready var name_label: Label = %NameLabel
@onready var thought_label: Label = %ThoughtLabel


func _ready() -> void:
	add_to_group("npc")
	_load_persona()
	WorldClock.tick.connect(_on_tick)
	# 先按当前时间立即决定一次目标
	_update_target_by_time()


func _physics_process(_delta: float) -> void:
	if not _moving:
		return
	var to_target: Vector2 = _target_pos - global_position
	if to_target.length() <= arrive_distance:
		_moving = false
		velocity = Vector2.ZERO
		return
	velocity = to_target.normalized() * move_speed
	move_and_slide()


func _load_persona() -> void:
	if persona_file == "" or not FileAccess.file_exists(persona_file):
		push_error("Animal: persona_file 无效 '%s'" % persona_file)
		return
	var f := FileAccess.open(persona_file, FileAccess.READ)
	var data = JSON.parse_string(f.get_as_text())
	f.close()
	if typeof(data) != TYPE_DICTIONARY:
		push_error("Animal: persona JSON 解析失败")
		return
	animal_id = data.get("id", "")
	animal_name = data.get("name", "无名")
	personality = data.get("personality", "")
	speech_style = data.get("speech_style", "")
	catchphrase = data.get("catchphrase", "")
	color_hex = data.get("color", color_hex)
	_schedule = data.get("schedule", [])

	if name_label:
		name_label.text = animal_name
	if sprite:
		sprite.color = Color(color_hex)


func _on_tick(_time_str: String, _total_minutes: int) -> void:
	_update_target_by_time()


## 根据当前小时找日程中"最后一个 ≤ 当前时间"的条目
func _update_target_by_time() -> void:
	if _schedule.is_empty():
		return
	var now_min: int = WorldClock.get_total_minutes() % (24 * 60)
	var picked: Dictionary = _schedule[0]
	for entry in _schedule:
		var t: String = entry.get("time", "00:00")
		var entry_min: int = _time_str_to_minutes(t)
		if entry_min <= now_min:
			picked = entry
		else:
			break

	var loc: String = picked.get("location", "")
	if loc != _target_location:
		_target_location = loc
		_target_pos = LocationDB.get_pos(loc)
		_current_intent = picked.get("intent", "")
		_moving = true
		if thought_label:
			thought_label.text = _current_intent


func _time_str_to_minutes(s: String) -> int:
	var parts := s.split(":")
	if parts.size() != 2:
		return 0
	return int(parts[0]) * 60 + int(parts[1])


## 提供给对话系统：当前心情/上下文一句话
func get_current_intent() -> String:
	return _current_intent


## 当前目标地点 id（如 "bakery"）
func get_target_location() -> String:
	return _target_location


## P0 占位对话；P0-2 起改为后端 LLM 返回
func get_placeholder_line() -> String:
	return "%s（这里以后接 LLM 对话）" % catchphrase
