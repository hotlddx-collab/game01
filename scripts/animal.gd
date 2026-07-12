extends CharacterBody2D
class_name Animal
## 怪物 NPC

signal affection_level_changed(prev_level: String, new_level: String)
##
## 加载 persona JSON（性格 + 日程 + sprite），按 WorldClock 时间走向目标地点。
## P1 行为规则：路网寻路 / 错峰出发 / 到达闲逛 / 沿途停顿 / 个性速度差异。

@export_file("*.json") var persona_file: String = ""
@export var move_speed: float = 80.0
@export var arrive_distance: float = 10.0

var animal_id: String = ""
var animal_name: String = ""
var species: String = ""
var personality: String = ""
var speech_style: String = ""
var catchphrase: String = ""
var sprite_file: String = ""

var _schedule: Array = []
var _schedule_weekend: Array = []  # 周末 schedule（game_day % 7 in [5,6]）
var _current_intent: String = "..."
var _target_location: String = ""
var _target_pos: Vector2 = Vector2.ZERO
var _last_dir: String = "down"

# ── 个性参数（从 persona JSON 的 movement 字段读取）──
var _mv_speed:   float = 1.0   # 速度倍率
var _mv_restless:float = 0.3   # 闲逛频率 0-1
var _mv_pause:   float = 0.08  # 沿途停顿概率/s
var _mv_wander:  float = 40.0  # 闲逛半径 px

# ── 5 状态机 ──
enum State { WAITING, TRAVELING, PAUSING, SETTLING, IDLE, WANDERING }
var _state: int = State.IDLE

var _depart_delay:  float = 0.0   # WAITING 倒计时
var _pause_timer:   float = 0.0   # PAUSING 倒计时
var _settle_timer:  float = 0.0   # SETTLING 倒计时
var _idle_timer:    float = 0.0   # IDLE 距离下次闲逛的倒计时
var _wander_target: Vector2 = Vector2.ZERO
var _wander_timer: float = 0.0
const WANDER_TIMEOUT: float = 4.0

# 卡死检测（NPC 撞墙不前进时跳过当前路径点）
var _stuck_timer: float = 0.0
var _last_progress_pos: Vector2 = Vector2.ZERO
const STUCK_TIMEOUT: float = 2.0
const STUCK_DIST: float = 4.0

# 路网路径队列（PathNetwork.find_path 的结果）
var _waypoint_queue: Array[Vector2] = []

# 当前正在前往的路径点（用直接距离判断到达，不依赖 is_navigation_finished）
var _current_wp_target: Vector2 = Vector2.ZERO

## NavigationAgent2D（动态创建）
var _nav_agent: NavigationAgent2D = null

@onready var sprite: AnimatedSprite2D = %AnimatedSprite2D
@onready var name_label: Label = %NameLabel
@onready var thought_label: Label = %ThoughtLabel
@onready var delta_label: Label = %DeltaLabel
@onready var interact_hint: Label = %InteractHint
@onready var emote_label: Label = %EmoteLabel
@onready var mood_label: Label = %MoodLabel

const NAME_COLORS := {
	"hate":    Color(1.0, 0.35, 0.35),
	"cold":    Color(1.0, 0.7,  0.7),
	"neutral": Color(1.0, 1.0,  1.0),
	"warm":    Color(1.0, 0.95, 0.55),
	"like":    Color(0.7, 1.0,  0.7),
	"love":    Color(0.35,1.0,  0.35),
}
var _affection_value: int = 0
var _affection_level: String = "neutral"
var _delta_tween: Tween = null
var _emote_tween: Tween = null
var _last_emote_time: float = 0.0  # 防 emote 刷屏
var _speaker_pop_tween: Tween = null

enum BusyState { FREE, TALKING_PLAYER, TALKING_NPC }
var _busy_state: int = BusyState.FREE
var _busy_until: float = 0.0

const INTENT_ARRIVE_DIST: float = 70.0
var _intent_active:     bool     = false
var _intent_target_pos: Vector2  = Vector2.ZERO
var _intent_callback:   Callable = Callable()


func _ready() -> void:
	add_to_group("npc")
	_setup_nav_agent()
	_load_persona()
	WorldClock.tick.connect(_on_tick)
	_state = State.IDLE
	_idle_timer = randf_range(1.0, 3.0)
	_update_target_by_time()


func _setup_nav_agent() -> void:
	_nav_agent = NavigationAgent2D.new()
	_nav_agent.path_desired_distance = 8.0
	_nav_agent.target_desired_distance = arrive_distance
	_nav_agent.avoidance_enabled = true
	_nav_agent.radius = 18.0
	_nav_agent.neighbor_distance = 80.0
	_nav_agent.max_neighbors = 8
	_nav_agent.max_speed = move_speed
	add_child(_nav_agent)


func _on_velocity_computed(_sv: Vector2) -> void:
	pass  # 不用此回调，直接在 _physics_process 里 move_and_slide


func _physics_process(delta: float) -> void:
	z_index = int(global_position.y / 4)
	_update_busy_timeout()

	if is_busy():
		velocity = Vector2.ZERO
		_update_animation()
		return

	# 自发意图优先（反思驱动走向另一 NPC）
	if _intent_active:
		if global_position.distance_to(_intent_target_pos) <= INTENT_ARRIVE_DIST:
			_intent_active = false
			_state = State.IDLE
			if _intent_callback.is_valid():
				_intent_callback.call()
		else:
			_follow_nav(delta)
		_update_animation()
		return

	var eff_speed := move_speed * _mv_speed

	match _state:
		# ── 等待出发（错峰）──
		State.WAITING:
			velocity = Vector2.ZERO
			_depart_delay -= delta
			if _depart_delay <= 0.0:
				_advance_waypoint()
				_state = State.TRAVELING

		# ── 行进中 ──
		State.TRAVELING:
			var to_wp: Vector2 = _current_wp_target - global_position
			var dist_to_wp := to_wp.length()
			if dist_to_wp < arrive_distance:
				# 到达当前路径点
				_stuck_timer = 0.0
				if _waypoint_queue.is_empty():
					_state = State.SETTLING
					_settle_timer = randf_range(0.8, 1.8)
				else:
					_advance_waypoint()
			else:
				# 优先用 NavAgent 寻路（绕过建筑/障碍）
				var next: Vector2 = _nav_agent.get_next_path_position()
				var dir: Vector2 = next - global_position
				# NavAgent 没有有效路径时（返回值就是当前位置）→ 直接朝目标走
				if dir.length() < 2.0:
					dir = to_wp
				if dir.length() > 2.0:
					velocity = dir.normalized() * eff_speed
					move_and_slide()
				else:
					velocity = Vector2.ZERO

				# 卡死检测：2 秒没移动 STUCK_DIST 像素 → 跳过当前路径点
				_stuck_timer += delta
				if global_position.distance_to(_last_progress_pos) > STUCK_DIST:
					_last_progress_pos = global_position
					_stuck_timer = 0.0
				elif _stuck_timer >= STUCK_TIMEOUT:
					_stuck_timer = 0.0
					if _waypoint_queue.is_empty():
						_state = State.SETTLING
						_settle_timer = 1.0
					else:
						_advance_waypoint()

				# 沿途随机暂停
				if randf() < _mv_pause * delta:
					_state = State.PAUSING
					_pause_timer = randf_range(0.6, 2.0)

		# ── 短暂停顿 ──
		State.PAUSING:
			velocity = Vector2.ZERO
			_pause_timer -= delta
			if _pause_timer <= 0.0:
				_state = State.TRAVELING

		# ── 刚到达，稳定一下 ──
		State.SETTLING:
			velocity = Vector2.ZERO
			_settle_timer -= delta
			if _settle_timer <= 0.0:
				_state = State.IDLE
				_idle_timer = randf_range(1.0, 2.5)  # 到达后很快开始闲逛

		# ── 待机：计时后随机闲逛 ──
		State.IDLE:
			velocity = Vector2.ZERO
			_idle_timer -= delta
			if _idle_timer <= 0.0:
				# 每次到期都以 restless 概率闲逛，否则再等一段
				if randf() < 0.5 + _mv_restless * 0.5:
					_start_wander()
				else:
					_idle_timer = randf_range(1.5, 3.5)

		# ── 闲逛 ──
		State.WANDERING:
			_wander_timer += delta
			var to_wander: Vector2 = _wander_target - global_position
			var dist_to_wander := to_wander.length()
			if dist_to_wander < arrive_distance or _wander_timer >= WANDER_TIMEOUT:
				_state = State.IDLE
				_idle_timer = randf_range(1.0, 3.0)
				velocity = Vector2.ZERO
			else:
				# 优先 NavAgent 路径（避开建筑），返回当前位置时降级直线
				var next: Vector2 = _nav_agent.get_next_path_position()
				var dir: Vector2 = next - global_position
				if dir.length() < 2.0:
					dir = to_wander
				if dir.length() > 2.0:
					velocity = dir.normalized() * eff_speed * 0.65
					move_and_slide()
				else:
					velocity = Vector2.ZERO

	_update_animation()


## 闲逛：以当前站立位置为中心随机走几步（不依赖建筑入口坐标）
func _start_wander() -> void:
	var rng := RandomNumberGenerator.new()
	rng.seed = (randi() ^ int(Time.get_ticks_msec())) & 0xFFFFFF
	var angle := rng.randf_range(0.0, TAU)
	var dist  := rng.randf_range(15.0, _mv_wander)
	# 以当前位置为中心，向随机方向走 dist 像素
	_wander_target = global_position + Vector2(cos(angle), sin(angle)) * dist
	_current_wp_target = _wander_target
	_nav_agent.target_position = _wander_target
	_wander_timer = 0.0
	_state = State.WANDERING


## 按路网路径前进：弹出下一个路径点提交给 NavAgent
func _advance_waypoint() -> void:
	if _waypoint_queue.is_empty():
		return
	var next_wp: Vector2 = _waypoint_queue.pop_front()
	_current_wp_target = next_wp
	_nav_agent.target_position = next_wp


## 意图追踪时的直接导航（走向 _intent_target_pos）
## 优先 NavAgent 绕障；无有效路径时降级直线（对齐 TRAVELING，避免卡死）
func _follow_nav(delta: float) -> void:
	var next: Vector2 = _nav_agent.get_next_path_position()
	var dir: Vector2 = next - global_position
	# NavAgent 没算出有效路径（返回当前位置 / 已判定完成）→ 直接朝目标直线走
	if dir.length() < 2.0 or _nav_agent.is_navigation_finished():
		dir = _intent_target_pos - global_position
	if dir.length() > 2.0:
		velocity = dir.normalized() * move_speed * _mv_speed
		move_and_slide()
	else:
		velocity = Vector2.ZERO


## 规划路网路径并进入 WAITING 状态（错峰出发）
func _plan_route_to(target: Vector2, immediate: bool = false) -> void:
	_target_pos = target
	_waypoint_queue = PathNetwork.find_path(global_position, target)
	if immediate:
		_depart_delay = 0.0
	else:
		_depart_delay = randf_range(0.0, 2.5)
	_state = State.WAITING


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
	animal_id   = data.get("id", "")
	animal_name = data.get("name", "无名")
	species     = data.get("species", "")
	personality = data.get("personality", "")
	speech_style= data.get("speech_style", "")
	catchphrase = data.get("catchphrase", "")
	sprite_file = data.get("sprite_file", "")
	_schedule         = data.get("schedule", [])
	_schedule_weekend = data.get("schedule_weekend", [])

	# 读取个性参数
	var mv: Dictionary = data.get("movement", {})
	_mv_speed   = float(mv.get("speed_factor",  1.0))
	_mv_restless= float(mv.get("restless",       0.3))
	_mv_pause   = float(mv.get("pause_chance",   0.08))
	_mv_wander  = float(mv.get("wander_radius",  40.0))
	_nav_agent.max_speed = move_speed * _mv_speed

	if name_label:
		name_label.text = animal_name
	_load_sprite_frames()


func _load_sprite_frames() -> void:
	if sprite == null or sprite_file == "":
		return
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


## NavRegion 烘焙后通知所有 NPC 刷新路径
func _refresh_nav_target() -> void:
	if _target_pos == Vector2.ZERO:
		return
	# 已在目标附近 → 直接进 IDLE，不重新走一圈
	if global_position.distance_to(_target_pos) < arrive_distance * 4.0:
		_state = State.IDLE
		_idle_timer = randf_range(0.5, 1.5)
	else:
		_plan_route_to(_target_pos, false)


## 根据当前时间决定目标
func _update_target_by_time() -> void:
	# 工作日 / 周末选择对应 schedule（game_day % 7 in [5,6] = 周末）
	var active_schedule: Array = _schedule
	if not _schedule_weekend.is_empty() and has_node("/root/WorldClock"):
		var day: int = WorldClock.get_day()
		if (day % 7) in [5, 6]:
			active_schedule = _schedule_weekend

	if active_schedule.is_empty():
		return
	var now_min: int = WorldClock.get_total_minutes() % (24 * 60)
	var picked: Dictionary = active_schedule[0]
	for entry in active_schedule:
		var entry_min: int = _time_str_to_minutes(entry.get("time", "00:00"))
		if entry_min <= now_min:
			picked = entry
		else:
			break
	var loc: String = picked.get("location", "")
	if loc != _target_location:
		_target_location = loc
		var base_pos: Vector2 = LocationDB.get_pos(loc)
		# 不再加 _location_offset - 目标直接是路网入口点（建筑门口在路上）
		# 多 NPC 同地点的散开靠 WANDERING 自然产生
		_current_intent = picked.get("intent", "")
		if thought_label:
			thought_label.text = _current_intent
		_plan_route_to(base_pos)


func _location_offset(loc_id: String) -> Vector2:
	if loc_id == "":
		return Vector2.ZERO
	var h: int = hash("%s|%s" % [animal_id, loc_id])
	var angle: float = float(h % 360) * PI / 180.0
	# 散布半径 30-55px（增大，避免多 NPC 叠加）
	var radius: float = 30.0 + float((h / 360) % 26)
	return Vector2(cos(angle), sin(angle)) * radius


func _time_str_to_minutes(s: String) -> int:
	var parts := s.split(":")
	if parts.size() != 2:
		return 0
	return int(parts[0]) * 60 + int(parts[1])


# ──── 公开接口 ────────────────────────────────

func get_current_intent() -> String: return _current_intent
func get_target_location() -> String: return _target_location
func get_animal_id()       -> String: return animal_id

func get_current_context() -> Dictionary:
	return {
		"time":           WorldClock.format_time() if has_node("/root/WorldClock") else "",
		"game_day":       WorldClock.get_day() if has_node("/root/WorldClock") else 0,
		"location":       _target_location,
		"location_label": LocationDB.get_label(_target_location) if _target_location else "",
		"intent":         _current_intent,
		"nearby_npcs":    _scan_nearby_npc_names(120.0),
	}


## 扫描周围范围内其他 NPC 的名字（供 prompt 注入"附近还有谁"）
func _scan_nearby_npc_names(radius: float) -> Array:
	var names: Array = []
	for n in get_tree().get_nodes_in_group("npc"):
		if n == self:
			continue
		if not is_instance_valid(n):
			continue
		if n.global_position.distance_to(global_position) > radius:
			continue
		var nm: String = n.animal_name if "animal_name" in n else ""
		if nm != "":
			names.append(nm)
	return names


# ──── 忙碌状态 ────────────────────────────────

func _update_busy_timeout() -> void:
	if _busy_state != BusyState.FREE and _busy_until > 0.0:
		if Time.get_ticks_msec() / 1000.0 > _busy_until:
			_busy_state = BusyState.FREE
			_busy_until  = 0.0

func is_busy() -> bool:
	_update_busy_timeout()
	return _busy_state != BusyState.FREE

func get_busy_state() -> int:
	is_busy()
	return _busy_state

func set_busy(state: int, duration: float = 0.0) -> void:
	_busy_state = state
	_busy_until = (Time.get_ticks_msec() / 1000.0 + duration) if duration > 0.0 else 0.0

func clear_busy() -> void:
	_busy_state = BusyState.FREE
	_busy_until  = 0.0
	_intent_active   = false
	_intent_callback = Callable()
	_update_target_by_time()

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


func approach_for_intent(target_pos: Vector2, on_arrive: Callable) -> void:
	_intent_target_pos = target_pos
	_intent_callback   = on_arrive
	_intent_active     = true
	_nav_agent.target_position = target_pos


const SPEECH_BUBBLE_SCENE := preload("res://scenes/ui/speech_bubble.tscn")

func show_speech_bubble(text: String, lifetime: float = 4.0) -> void:
	if text == "":
		return
	var bubble := SPEECH_BUBBLE_SCENE.instantiate()
	add_child(bubble)
	bubble.position = Vector2(0, -36)
	bubble.show_text(animal_name, text, lifetime)
	_pop_speaker()


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


func get_placeholder_line() -> String:
	return "%s（这里以后接 LLM 对话）" % catchphrase


func set_interact_hint(active: bool) -> void:
	if interact_hint == null:
		return
	interact_hint.visible = active
	if active:
		var c: Color = Color(1, 0.4, 0.4) if is_busy() else Color(1, 0.9, 0.3)
		interact_hint.add_theme_color_override("font_color", c)


# ──── 好感度 ─────────────────────────────────

func update_affection(value: int, level: String, delta: int) -> void:
	var prev_level := _affection_level
	_affection_value = value
	_affection_level = level
	_apply_name_color(level)
	if delta != 0:
		_show_delta(delta)
		# 礼物 / 互动后的反应 emote
		if delta >= 5:
			show_emote("❤️", 2.0, 0.0)
		elif delta > 0:
			show_emote("😊", 1.5, 0.0)
		elif delta <= -5:
			show_emote("💔", 2.0, 0.0)
		else:
			show_emote("😞", 1.5, 0.0)
	# 等级跃迁 → 里程碑事件（下一步实现）
	if prev_level != level:
		affection_level_changed.emit(prev_level, level)

func get_affection()       -> int:    return _affection_value
func get_affection_level() -> String: return _affection_level

## 头顶常驻心情表情。平静时隐藏（避免刷屏），其余档位常驻。
func set_mood(emote: String, level: String) -> void:
	if mood_label == null:
		return
	if emote == "" or level == "calm":
		mood_label.visible = false
		return
	mood_label.text = emote
	mood_label.visible = true

func _apply_name_color(level: String) -> void:
	if name_label == null:
		return
	var c: Color = NAME_COLORS.get(level, Color.WHITE)
	name_label.add_theme_color_override("font_color", c)

func _show_delta(delta: int) -> void:
	if delta_label == null:
		return
	var icon: String = "❤️" if delta > 0 else "💔"
	# 心数按档位封顶（最多 3 颗），只显示心图标，不带数字
	var mag: int = absi(delta)
	var hearts: int = 1
	if mag >= 15:
		hearts = 3
	elif mag >= 8:
		hearts = 2
	delta_label.text = icon.repeat(hearts)
	delta_label.modulate = Color(1, 1, 1, 1)
	delta_label.visible = true
	delta_label.position = Vector2(-60, -100)
	if _delta_tween and _delta_tween.is_valid():
		_delta_tween.kill()
	_delta_tween = create_tween().set_parallel(true)
	_delta_tween.tween_property(delta_label, "position:y", -120.0, 1.0)
	_delta_tween.tween_property(delta_label, "modulate:a",   0.0,   1.0).set_delay(0.4)
	_delta_tween.chain().tween_callback(func():
		if delta_label:
			delta_label.visible = false
			delta_label.modulate.a = 1.0
	)


# ──── 头顶 Emote 气泡 ────────────────────────

## 显示头顶 emote（emoji），自动淡出
## icon: "❗" "❓" "😊" "😠" "🎁" "💭" "💤" 等
## duration: 显示时长（秒），默认 1.8s
## min_interval: 距上次 emote 的最小间隔，避免刷屏
func show_emote(icon: String, duration: float = 1.8, min_interval: float = 1.5) -> void:
	if emote_label == null or icon == "":
		return
	# 防刷屏
	var now: float = Time.get_ticks_msec() / 1000.0
	if now - _last_emote_time < min_interval:
		return
	_last_emote_time = now

	emote_label.text = icon
	emote_label.modulate = Color(1, 1, 1, 0.0)
	emote_label.visible = true
	# 起始位置略低，向上飘
	emote_label.position = Vector2(-16, -68)

	if _emote_tween and _emote_tween.is_valid():
		_emote_tween.kill()
	_emote_tween = create_tween()
	# 弹出（淡入 + 上飘 6px + 微缩放）
	_emote_tween.set_parallel(true)
	_emote_tween.tween_property(emote_label, "modulate:a", 1.0, 0.15)
	_emote_tween.tween_property(emote_label, "position:y", -78.0, 0.25)\
		.set_trans(Tween.TRANS_BACK).set_ease(Tween.EASE_OUT)
	# 等 duration 后淡出
	_emote_tween.chain().tween_property(emote_label, "modulate:a", 0.0, 0.4)\
		.set_delay(duration)
	_emote_tween.chain().tween_callback(func():
		if emote_label:
			emote_label.visible = false
	)
