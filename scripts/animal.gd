extends CharacterBody2D
class_name Animal
## 怪物 NPC
##
## 加载 persona JSON（性格 + 日程 + sprite），按 WorldClock 时间走向目标地点。
## AnimatedSprite2D 根据移动方向播放动画。头顶 Label 显示名字+当前 intent。
## 玩家可用 E 触发对话。
##
## P0 用直线 move_toward 移动；P1+ 换 NavigationAgent2D。

@export_file("*.json") var persona_file: String = ""
@export var move_speed: float = 80.0
@export var arrive_distance: float = 6.0

var animal_id: String = ""
var animal_name: String = ""
var species: String = ""
var personality: String = ""
var speech_style: String = ""
var catchphrase: String = ""
var sprite_file: String = ""

var _schedule: Array = []          # [{time, location, intent}]
var _current_intent: String = "..."
var _target_location: String = ""
var _target_pos: Vector2 = Vector2.ZERO
var _moving: bool = false
var _last_dir: String = "down"

@onready var sprite: AnimatedSprite2D = %AnimatedSprite2D
@onready var name_label: Label = %NameLabel
@onready var thought_label: Label = %ThoughtLabel
@onready var delta_label: Label = %DeltaLabel
@onready var interact_hint: Label = %InteractHint

# 好感等级 → 名字板字色
const NAME_COLORS := {
	"hate":    Color(1.0, 0.35, 0.35),  # 红
	"cold":    Color(1.0, 0.7, 0.7),    # 淡红
	"neutral": Color(1.0, 1.0, 1.0),    # 白（默认）
	"warm":    Color(1.0, 0.95, 0.55),  # 浅黄（开始有好感）
	"like":    Color(0.7, 1.0, 0.7),    # 淡绿
	"love":    Color(0.35, 1.0, 0.35),  # 绿
}

var _affection_value: int = 0
var _affection_level: String = "neutral"
var _delta_tween: Tween = null
var _speaker_pop_tween: Tween = null

# ---------- 状态机 ----------
enum BusyState { FREE, TALKING_PLAYER, TALKING_NPC }
var _busy_state: int = BusyState.FREE
var _busy_until: float = 0.0  # 0 = 无限直到手动 clear；>0 = 到点自动 clear


func _ready() -> void:
	add_to_group("npc")
	_load_persona()
	WorldClock.tick.connect(_on_tick)
	# 先按当前时间立即决定一次目标
	_update_target_by_time()


func _physics_process(_delta: float) -> void:
	# busy 状态：停止移动（朝向已由 set_busy/face_to 设过）
	if is_busy():
		velocity = Vector2.ZERO
		_update_animation()
		return
	if _moving:
		var to_target: Vector2 = _target_pos - global_position
		if to_target.length() <= arrive_distance:
			_moving = false
			velocity = Vector2.ZERO
		else:
			velocity = to_target.normalized() * move_speed
			move_and_slide()
	else:
		velocity = Vector2.ZERO
	_update_animation()


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
	species = data.get("species", "")
	personality = data.get("personality", "")
	speech_style = data.get("speech_style", "")
	catchphrase = data.get("catchphrase", "")
	sprite_file = data.get("sprite_file", "")
	_schedule = data.get("schedule", [])

	if name_label:
		name_label.text = animal_name
	_load_sprite_frames()


func _load_sprite_frames() -> void:
	if sprite == null or sprite_file == "":
		return
	# 已在场景里挂了 SpriteFrames（编辑器配置）→ 不覆盖
	if sprite.sprite_frames != null:
		return
	var sf := SpriteFactory.build_frames_from_path(sprite_file)
	if sf == null:
		push_warning("Animal[%s]: 加载 sprite 失败 %s" % [animal_id, sprite_file])
		return
	sprite.sprite_frames = sf
	sprite.play("idle")


func _update_animation() -> void:
	if sprite == null or sprite.sprite_frames == null:
		return
	var dir := SpriteFactory.direction_from_velocity(velocity)
	if dir == "":
		# idle 时保持上次水平翻转
		if _last_dir == "left":
			sprite.flip_h = true
		elif _last_dir == "right":
			sprite.flip_h = false
		if sprite.animation != "idle":
			sprite.play("idle")
	else:
		if dir == "left" or dir == "right":
			sprite.flip_h = SpriteFactory.direction_needs_flip(dir)
			_last_dir = dir
		else:
			_last_dir = dir
		if sprite.animation != "walk":
			sprite.play("walk")


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
		# 站位错开：每个 NPC 在 location 中心周围按 (animal_id+loc) 哈希分配确定性偏移
		var base_pos: Vector2 = LocationDB.get_pos(loc)
		_target_pos = base_pos + _location_offset(loc)
		_current_intent = picked.get("intent", "")
		_moving = true
		if thought_label:
			thought_label.text = _current_intent


## 站位错开：基于 animal_id + location 生成确定性偏移
## 同 NPC 同 location 偏移恒定，避免抖动；不同 NPC 错开避免堆叠
func _location_offset(loc_id: String) -> Vector2:
	if loc_id == "":
		return Vector2.ZERO
	var h: int = hash("%s|%s" % [animal_id, loc_id])
	var angle: float = float(h % 360) * PI / 180.0
	var radius: float = 18.0 + float((h / 360) % 18)  # 18~35px
	return Vector2(cos(angle), sin(angle)) * radius


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


## 暴露 animal_id（供 ChatManager 等用 has_method 探测）
func get_animal_id() -> String:
	return animal_id


## 当前完整上下文（用于 NPC 互动时给后端）
func get_current_context() -> Dictionary:
	return {
		"time": WorldClock.format_time() if has_node("/root/WorldClock") else "",
		"game_day": WorldClock.get_day() if has_node("/root/WorldClock") else 0,
		"location": _target_location,
		"location_label": LocationDB.get_label(_target_location) if _target_location else "",
		"intent": _current_intent,
	}


const SPEECH_BUBBLE_SCENE := preload("res://scenes/ui/speech_bubble.tscn")

## NPC 头顶弹气泡说一句（自动用自己的名字作 speaker）
func show_speech_bubble(text: String, lifetime: float = 4.0) -> void:
	if text == "":
		return
	var bubble := SPEECH_BUBBLE_SCENE.instantiate()
	add_child(bubble)
	bubble.position = Vector2(0, -36)  # 头顶起点（气泡内部还会自动上移整个高度）
	bubble.show_text(animal_name, text, lifetime)
	_pop_speaker()


## 说话时 sprite 短暂弹一下，让玩家看清是谁在说
func _pop_speaker() -> void:
	if sprite == null:
		return
	if _speaker_pop_tween and _speaker_pop_tween.is_valid():
		_speaker_pop_tween.kill()
	sprite.scale = Vector2.ONE
	_speaker_pop_tween = create_tween()
	_speaker_pop_tween.tween_property(sprite, "scale", Vector2(1.18, 1.18), 0.12)\
		.set_trans(Tween.TRANS_QUAD).set_ease(Tween.EASE_OUT)
	_speaker_pop_tween.tween_property(sprite, "scale", Vector2.ONE, 0.18)\
		.set_trans(Tween.TRANS_QUAD).set_ease(Tween.EASE_IN)


## 占位对话；P0-2 起改为后端 LLM 返回
func get_placeholder_line() -> String:
	return "%s（这里以后接 LLM 对话）" % catchphrase


## 玩家 hover 提示：显示/隐藏头顶 ▼ 箭头
func set_interact_hint(active: bool) -> void:
	if interact_hint == null:
		return
	interact_hint.visible = active
	# busy 时变红，提示玩家"对方正在交谈"
	if active:
		var c: Color = Color(1, 0.4, 0.4) if is_busy() else Color(1, 0.9, 0.3)
		interact_hint.add_theme_color_override("font_color", c)


# ---------- 状态机 ----------

func is_busy() -> bool:
	if _busy_state == BusyState.FREE:
		return false
	# 自动过期
	if _busy_until > 0.0 and Time.get_ticks_msec() / 1000.0 > _busy_until:
		_busy_state = BusyState.FREE
		_busy_until = 0.0
		return false
	return true


func get_busy_state() -> int:
	# 顺便触发自动过期
	is_busy()
	return _busy_state


## 设置 busy。duration<=0 表示无限直到 clear_busy()。
func set_busy(state: int, duration: float = 0.0) -> void:
	_busy_state = state
	if duration > 0.0:
		_busy_until = Time.get_ticks_msec() / 1000.0 + duration
	else:
		_busy_until = 0.0
	# busy 期间不再前进，velocity 由 _physics_process 清零
	_moving = false


func clear_busy() -> void:
	_busy_state = BusyState.FREE
	_busy_until = 0.0
	# 重新拉起目标，让 NPC 继续日程
	_update_target_by_time()


## 朝向某点（简单 horizontal flip + last_dir 记录，立即生效不依赖 velocity）
func face_to(target_pos: Vector2) -> void:
	if sprite == null:
		return
	var dx: float = target_pos.x - global_position.x
	var dy: float = target_pos.y - global_position.y
	if abs(dx) > abs(dy):
		if dx >= 0:
			sprite.flip_h = SpriteFactory.direction_needs_flip("right")
			_last_dir = "right"
		else:
			sprite.flip_h = SpriteFactory.direction_needs_flip("left")
			_last_dir = "left"
	else:
		_last_dir = "down" if dy > 0 else "up"


# ---------- 好感度 ----------

## 后端发回的好感度更新（值、等级、本次 delta）。
## 由 main.gd 在 AgentClient.affection_changed 信号里转发过来。
func update_affection(value: int, level: String, delta: int) -> void:
	_affection_value = value
	_affection_level = level
	_apply_name_color(level)
	if delta != 0:
		_show_delta(delta)


func get_affection() -> int:
	return _affection_value


func get_affection_level() -> String:
	return _affection_level


func _apply_name_color(level: String) -> void:
	if name_label == null:
		return
	var c: Color = NAME_COLORS.get(level, Color.WHITE)
	name_label.add_theme_color_override("font_color", c)


func _show_delta(delta: int) -> void:
	if delta_label == null:
		return
	var icon: String = "❤️" if delta > 0 else "💔"
	var n: int = absi(delta)
	delta_label.text = icon.repeat(n)
	# emoji 自带颜色，让 modulate 保持白色不染色
	delta_label.modulate = Color(1, 1, 1, 1)
	delta_label.visible = true
	delta_label.position = Vector2(-60, -100)

	if _delta_tween and _delta_tween.is_valid():
		_delta_tween.kill()
	_delta_tween = create_tween().set_parallel(true)
	_delta_tween.tween_property(delta_label, "position:y", -120.0, 1.0)
	_delta_tween.tween_property(delta_label, "modulate:a", 0.0, 1.0).set_delay(0.4)
	_delta_tween.chain().tween_callback(func ():
		if delta_label:
			delta_label.visible = false
			delta_label.modulate.a = 1.0
	)
