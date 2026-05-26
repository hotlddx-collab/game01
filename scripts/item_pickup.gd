extends Area2D
class_name ItemPickup
## 散落物品（玩家走近按 E 拾取）
##
## 用法：场景里实例化此节点，设置 item_id（ItemDB 已知 ID）。
## _ready 时自动从 ItemDB 加载 icon。
## 玩家进入 Area2D 范围 → 触发 ▼ 提示；按 E → 加进 PlayerInventory，节点 queue_free。

signal picked_up(item_id: String)

@export var item_id: String = "flower"
@export var pickup_radius: float = 28.0

var _hovered: bool = false

@onready var sprite: Sprite2D = %Sprite2D
@onready var name_label: Label = %NameLabel
@onready var hint_label: Label = %HintLabel
@onready var collision: CollisionShape2D = %CollisionShape2D


func _ready() -> void:
	add_to_group("interactable")
	add_to_group("pickup")
	monitoring = true
	monitorable = false
	# 半径
	var sh := CircleShape2D.new()
	sh.radius = pickup_radius
	collision.shape = sh
	# icon
	var tex: Texture2D = ItemDB.get_icon(item_id)
	if tex != null:
		sprite.texture = tex
	# 名字标签
	if name_label:
		name_label.text = ItemDB.get_item_name(item_id)
	# 提示默认隐藏
	if hint_label:
		hint_label.visible = false
	# 缓慢上下漂浮（生动一点）
	var tw := create_tween().set_loops()
	tw.tween_property(sprite, "position:y", -2.0, 1.0).set_trans(Tween.TRANS_SINE).set_ease(Tween.EASE_IN_OUT)
	tw.tween_property(sprite, "position:y", 2.0, 1.0).set_trans(Tween.TRANS_SINE).set_ease(Tween.EASE_IN_OUT)


## 供 player.gd 通用接口：是否是 NPC（false 表示拾取物）
func get_animal_id() -> String:
	return ""


## 玩家 hover 提示
func set_interact_hint(active: bool) -> void:
	_hovered = active
	if hint_label:
		hint_label.visible = active


## 玩家按 E 时调用：拾取
func pickup() -> void:
	if not ItemDB.has(item_id):
		queue_free()
		return
	PlayerInventory.add_item(item_id, 1)
	picked_up.emit(item_id)
	# 弹出 ❤️ 之类反馈交给 PlayerInventory.item_added 接收方做
	queue_free()
