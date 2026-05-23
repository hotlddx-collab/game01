extends CharacterBody2D
## 玩家角色
##
## WASD/方向键 移动，E 与最近的 NPC 交互。
## Camera2D 子节点跟随。
## input_enabled = false 时（如对话中）冻结移动与交互输入。

signal interact_pressed(target: Node)

@export var move_speed: float = 120.0
@export var interact_radius: float = 48.0
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

# NPC 列表由主场景注册（也可全局组）
var _nearby_npcs: Array[Node] = []


func _physics_process(_delta: float) -> void:
	if not input_enabled:
		velocity = Vector2.ZERO
		move_and_slide()
		return
	var input_vec := Vector2(
		Input.get_axis("move_left", "move_right"),
		Input.get_axis("move_up", "move_down")
	)
	if input_vec.length() > 1.0:
		input_vec = input_vec.normalized()
	velocity = input_vec * move_speed
	move_and_slide()


func _unhandled_input(event: InputEvent) -> void:
	if not input_enabled:
		return
	if event.is_action_pressed("interact"):
		var target := _find_closest_npc()
		if target != null:
			interact_pressed.emit(target)


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
