extends CharacterBody2D
## 玩家角色
##
## WASD/方向键 移动，E 与最近的 NPC 交互。
## Camera2D 子节点跟随。AnimatedSprite2D 子节点根据移动方向播放动画。
## input_enabled = false 时（如对话中）冻结移动与交互输入。

signal interact_pressed(target: Node)

@export var move_speed: float = 120.0
@export var interact_radius: float = 48.0
@export_file("*.png") var sprite_file: String = "res://assets/characters/player.png"
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

@onready var sprite: AnimatedSprite2D = %AnimatedSprite2D


func _ready() -> void:
	add_to_group("player")
	_load_sprite_frames()


func _physics_process(_delta: float) -> void:
	if not input_enabled:
		velocity = Vector2.ZERO
		move_and_slide()
		_play_idle()
		_update_hover_target()
		return
	var input_vec := Vector2(
		Input.get_axis("move_left", "move_right"),
		Input.get_axis("move_up", "move_down")
	)
	if input_vec.length() > 1.0:
		input_vec = input_vec.normalized()
	velocity = input_vec * move_speed
	move_and_slide()
	_update_animation()
	_update_hover_target()


## 每帧更新当前 hover 的 NPC，并切换 ▼ 箭头显示
func _update_hover_target() -> void:
	var target: Node = null
	if input_enabled:
		target = _find_closest_npc()
	if target == _hover_target:
		return
	if _hover_target and is_instance_valid(_hover_target) and _hover_target.has_method("set_interact_hint"):
		_hover_target.set_interact_hint(false)
	_hover_target = target
	if _hover_target and _hover_target.has_method("set_interact_hint"):
		_hover_target.set_interact_hint(true)


func _unhandled_input(event: InputEvent) -> void:
	if not input_enabled:
		return
	if event.is_action_pressed("interact"):
		var target := _find_closest_npc()
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
