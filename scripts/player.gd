extends CharacterBody2D
## 玩家角色
##
## WASD/方向键 移动，E 与最近的 NPC 交互。
## Camera2D 子节点跟随。AnimatedSprite2D 子节点根据移动方向播放动画。
## input_enabled = false 时（如对话中）冻结移动与交互输入。

signal interact_pressed(target: Node)
## 体力槽变化：cur/max，是否疲劳，是否处于冲刺态（槽变黄）
signal stamina_changed(cur: float, max_value: float, tired: bool, boosted: bool)

@export var move_speed: float = 120.0
@export var interact_radius: float = 48.0
@export_file("*.png") var sprite_file: String = "res://assets/characters/player.png"

@export_group("体力")
@export var stamina_max: float = 100.0
## 一整个游戏日耗尽体力。1 游戏日 = 15 现实分钟 = 900 秒。
## 按时间线性衰减，不分走动/站立，也没有自动回复——只能回家睡觉补满。
@export var stamina_day_seconds: float = 900.0
## 体力低于此比例进入疲劳态，开始降速
@export var tired_threshold: float = 0.35
## 体力耗尽时的速度倍率（疲劳区间内从 1.0 线性插值到该值）
@export var tired_speed_scale: float = 0.5

@export_group("冲刺")
## 睡醒后同一条槽变成黄色冲刺条：自动加速，走完这段秒数后
## 槽变回蓝色体力条，从满值继续按游戏日流逝。
@export var sprint_seconds: float = 60.0
## 冲刺态的速度倍率
@export var sprint_speed_scale: float = 1.7


## 主控开关。对话开启时设 false。
var input_enabled: bool = true:
	set(value):
		# 从禁用切回启用时，清除可能残留的方向键动作状态（拼音输入法残留）
		if value and not input_enabled:
			Input.action_release("move_up")
			Input.action_release("move_down")
			Input.action_release("move_left")
			Input.action_release("move_right")
			Input.action_release("interact")
			velocity = Vector2.ZERO
		input_enabled = value

var _last_dir: String = "down"
var _hover_target: Node = null
var _stamina: float = 0.0
## 冲刺态剩余秒数。>0 时同一条槽显示为黄色冲刺条并自动加速。
var _sprint_left: float = 0.0
## 上一帧上报的疲劳态，用于只在跨档时发信号
var _was_tired: bool = false

@onready var sprite: AnimatedSprite2D = %AnimatedSprite2D


func _ready() -> void:
	add_to_group("player")
	_stamina = stamina_max
	_load_sprite_frames()
	_emit_stamina()


func _physics_process(delta: float) -> void:
	# Y轴排序遮挡（与 animal.gd 公式一致）
	z_index = int(global_position.y / 4)
	if not input_enabled:
		velocity = Vector2.ZERO
		move_and_slide()
		_play_idle()
		_update_hover_target()
		_update_stamina(delta)
		return
	var input_vec := Vector2(
		Input.get_axis("move_left", "move_right"),
		Input.get_axis("move_up", "move_down")
	)
	if input_vec.length() > 1.0:
		input_vec = input_vec.normalized()
	_update_stamina(delta)
	velocity = input_vec * move_speed * _current_speed_scale()
	move_and_slide()
	_update_animation()
	_update_hover_target()


## 一条槽两种状态：
##   冲刺态（睡醒后 sprint_seconds 秒内）——槽是满的黄条，按冲刺秒数倒着掉，自动加速；
##   普通态——蓝色体力条，按游戏日时长线性流逝，不会自己回，只能睡觉补满。
## 冲刺秒数走完的那一刻，体力补满并切回普通态。
func _update_stamina(delta: float) -> void:
	if _sprint_left > 0.0:
		_sprint_left = max(_sprint_left - delta, 0.0)
		if _sprint_left == 0.0:
			# 冲刺结束 → 槽变回蓝色体力条，从满值开始过这一天
			_stamina = stamina_max
		_emit_stamina()
		return
	if _stamina <= 0.0:
		return
	var rate: float = stamina_max / stamina_day_seconds if stamina_day_seconds > 0.0 else 0.0
	_stamina = max(_stamina - rate * delta, 0.0)
	_emit_stamina()


## 当前速度倍率：冲刺态直接加速；否则按体力的疲劳程度降速
func _current_speed_scale() -> float:
	if _sprint_left > 0.0:
		return sprint_speed_scale
	var ratio := _stamina / stamina_max if stamina_max > 0.0 else 1.0
	if ratio >= tired_threshold:
		return 1.0
	# ratio 从 tired_threshold 掉到 0 → 倍率从 1.0 掉到 tired_speed_scale
	var t := ratio / tired_threshold if tired_threshold > 0.0 else 0.0
	return lerpf(tired_speed_scale, 1.0, t)


## 上报槽状态。冲刺态下槽按剩余冲刺秒数显示，普通态下按体力显示。
func _emit_stamina() -> void:
	if _sprint_left > 0.0:
		_was_tired = false
		stamina_changed.emit(_sprint_left, sprint_seconds, false, true)
		return
	var tired := _stamina / stamina_max < tired_threshold if stamina_max > 0.0 else false
	_was_tired = tired
	stamina_changed.emit(_stamina, stamina_max, tired, false)


## 睡觉结算：先进入一段冲刺态（槽变黄 + 加速），走完后自动回满体力
func rest() -> void:
	_stamina = stamina_max
	_sprint_left = sprint_seconds
	_emit_stamina()


## 每帧更新当前 hover 的 NPC，并切换 ▼ 箭头显示
func _update_hover_target() -> void:
	var target: Node = null
	if input_enabled:
		target = _find_closest_interactable()
	if target == _hover_target:
		return
	if _hover_target and is_instance_valid(_hover_target) and _hover_target.has_method("set_interact_hint"):
		_hover_target.set_interact_hint(false)
	_hover_target = target
	if _hover_target and _hover_target.has_method("set_interact_hint"):
		_hover_target.set_interact_hint(true)
	# 玩家靠近 NPC 时，NPC 根据好感度做反应
	if _hover_target and _hover_target.has_method("show_emote") and _hover_target.has_method("get_affection_level"):
		var lvl: String = _hover_target.get_affection_level()
		var emote := ""
		match lvl:
			"intimate": emote = "❤️"
			"close":    emote = "😊"
			"fond":     emote = "🙂"
			"friendly": emote = "👋"
			"hostile":  emote = "😠"
			_:          emote = ""  # neutral 不显示
		if emote != "":
			_hover_target.show_emote(emote, 1.5, 8.0)  # 8秒内不重复


func _unhandled_input(event: InputEvent) -> void:
	if not input_enabled:
		return
	if event.is_action_pressed("interact"):
		var target := _find_closest_interactable()
		if target != null:
			interact_pressed.emit(target)


func _load_sprite_frames() -> void:
	if sprite == null:
		return
	# 已在场景里挂了 SpriteFrames（编辑器配置）→ 不覆盖
	if sprite.sprite_frames != null:
		return
	if sprite_file == "":
		return
	var sf := SpriteFactory.build_frames_from_path(sprite_file)
	if sf == null:
		push_warning("Player: 加载 sprite 失败 %s" % sprite_file)
		return
	sprite.sprite_frames = sf
	sprite.play("idle")


func _update_animation() -> void:
	if sprite == null or sprite.sprite_frames == null:
		return
	var dir := SpriteFactory.direction_from_velocity(velocity)
	if dir == "":
		_play_idle()
	else:
		# 维持上次"是否朝左"判断，仅当当前移动到左/右时才更新 flip
		if dir == "left" or dir == "right":
			sprite.flip_h = SpriteFactory.direction_needs_flip(dir)
			_last_dir = dir
		else:
			_last_dir = dir
		if sprite.animation != "walk":
			sprite.play("walk")


func _play_idle() -> void:
	if sprite == null or sprite.sprite_frames == null:
		return
	# idle 时保持上次的水平翻转
	if _last_dir == "left":
		sprite.flip_h = true
	elif _last_dir == "right":
		sprite.flip_h = false
	if sprite.animation != "idle":
		sprite.play("idle")


func _find_closest_npc() -> Node:
	var npcs := get_tree().get_nodes_in_group("npc")
	var closest: Node = null
	var min_dist: float = interact_radius
	for n in npcs:
		if not (n is Node2D):
			continue
		var d: float = global_position.distance_to((n as Node2D).global_position)
		if d < min_dist:
			min_dist = d
			closest = n
	return closest


## 找最近的可交互对象（NPC / ItemPickup / 休息点）
## NPC 在 group "npc"；ItemPickup 在 group "pickup"；玩家的家在 group "rest"
func _find_closest_interactable() -> Node:
	var closest: Node = null
	var min_dist: float = interact_radius
	for group_name in ["npc", "pickup", "rest"]:
		for n in get_tree().get_nodes_in_group(group_name):
			if not (n is Node2D):
				continue
			# 休息点按门口（入口点）判定，否则站在屋顶也能触发
			var p: Vector2 = (n as Node2D).global_position
			if group_name == "rest" and n.has_method("get_entry_position"):
				p = n.get_entry_position()
			var d: float = global_position.distance_to(p)
			if d < min_dist:
				min_dist = d
				closest = n
	return closest
