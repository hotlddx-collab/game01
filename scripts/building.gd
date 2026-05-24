@tool
class_name Building
extends Node2D
## 建筑场景
##
## 支持两种视觉模式：
##   1. 像素小屋模式（sprite_texture + sprite_region 设置时）
##   2. 占位色块模式（仅 building_color，未设贴图时）
##
## 共同提供：
##   - 头顶名字 Label
##   - EntryPoint Marker2D（NPC 寻路目标）
##   - LocationDB 自注册

signal entered(actor: Node)
signal exited(actor: Node)

@export var building_id: String = "":
	set(value):
		building_id = value
		_update_visual()

@export var display_name: String = "建筑":
	set(value):
		display_name = value
		_update_visual()

## 占位色块模式的尺寸（仅在 sprite_texture 未设置时用）
@export var size: Vector2 = Vector2(100.0, 100.0):
	set(value):
		size = value
		_update_visual()

## 占位色块模式的颜色
@export var building_color: Color = Color(0.8, 0.8, 0.8, 1.0):
	set(value):
		building_color = value
		_update_visual()

## 像素小屋贴图（如 TilesetHouse.png）。设置后切换到 sprite 模式。
@export var sprite_texture: Texture2D:
	set(value):
		sprite_texture = value
		_update_visual()

## 在 sprite_texture 上的子区域 Rect2(x, y, w, h)，单位像素
@export var sprite_region: Rect2 = Rect2(0, 0, 48, 48):
	set(value):
		sprite_region = value
		_update_visual()

## NPC 走向此建筑时的目标点（相对建筑中心的偏移）
@export var entry_offset: Vector2 = Vector2.ZERO:
	set(value):
		entry_offset = value
		_update_entry_point()


func _ready() -> void:
	_update_visual()
	if Engine.is_editor_hint():
		return
	add_to_group("building")
	if has_node("/root/LocationDB"):
		LocationDB.register(self)


func _exit_tree() -> void:
	if Engine.is_editor_hint():
		return
	if has_node("/root/LocationDB"):
		LocationDB.unregister(self)


func _update_visual() -> void:
	if not is_inside_tree():
		return
	var color_rect := get_node_or_null("Sprite") as ColorRect
	var house_sprite := get_node_or_null("HouseSprite") as Sprite2D
	var lbl := get_node_or_null("NameLabel") as Label

	# 决定显示哪种视觉
	var use_sprite := sprite_texture != null
	var visual_size: Vector2
	if use_sprite:
		visual_size = sprite_region.size
		if color_rect:
			color_rect.visible = false
		if house_sprite:
			house_sprite.visible = true
			var atlas := AtlasTexture.new()
			atlas.atlas = sprite_texture
			atlas.region = sprite_region
			house_sprite.texture = atlas
			house_sprite.position = Vector2.ZERO  # 中心对齐
	else:
		visual_size = size
		if color_rect:
			color_rect.visible = true
			color_rect.size = size
			color_rect.position = -size * 0.5
			color_rect.color = building_color
		if house_sprite:
			house_sprite.visible = false

	# Label 跟随视觉尺寸
	if lbl:
		lbl.text = display_name
		lbl.size = Vector2(visual_size.x + 60.0, 24.0)
		lbl.position = Vector2(-visual_size.x * 0.5 - 30.0, -visual_size.y * 0.5 - 28.0)

	_update_entry_point()


func _update_entry_point() -> void:
	if not is_inside_tree():
		return
	var ep := get_node_or_null("EntryPoint") as Marker2D
	if ep:
		ep.position = entry_offset


## NPC 寻路目标
func get_entry_position() -> Vector2:
	return global_position + entry_offset


## 建筑中心
func get_center_position() -> Vector2:
	return global_position
