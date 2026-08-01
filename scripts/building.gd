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

## 关闭后隐藏色块视觉（仅保留 LocationDB 注册 + 名字 Label）。
## 广场等"地面区域"用此模式，避免遮挡角色。
@export var show_area_visual: bool = true:
	set(value):
		show_area_visual = value
		_update_visual()

## 玩家的家：开启后入口处可按 E 休息，靠近时头顶弹出休息提示
@export var is_rest_spot: bool = false


func _ready() -> void:
	_update_visual()
	# _update_visual 里已调用 _update_z_index，但 global_position 在 _ready 时才稳定，补一次
	var visual_size := sprite_region.size if sprite_texture != null else size
	_update_z_index(visual_size)
	if Engine.is_editor_hint():
		return
	add_to_group("building")
	if is_rest_spot:
		add_to_group("rest")
		_ensure_rest_hint()
	if has_node("/root/LocationDB"):
		LocationDB.register(self)


## 休息提示标签（默认隐藏，玩家靠近时由 set_interact_hint 打开）
func _ensure_rest_hint() -> void:
	if get_node_or_null("RestHint") != null:
		return
	var lbl := Label.new()
	lbl.name = "RestHint"
	lbl.visible = false
	lbl.text = "🛏 按 E 休息"
	lbl.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	# 与其他世界空间文字一致：2 倍字号渲染再缩回，抵消相机 zoom 造成的模糊
	lbl.add_theme_font_size_override("font_size", 24)
	lbl.add_theme_color_override("font_color", Color(1, 0.95, 0.7, 1))
	lbl.add_theme_color_override("font_outline_color", Color(0, 0, 0, 0.95))
	lbl.add_theme_constant_override("outline_size", 8)
	lbl.size = Vector2(280, 30)
	lbl.scale = Vector2(0.5, 0.5)
	lbl.position = Vector2(-70.0 + entry_offset.x, entry_offset.y + 6.0)
	add_child(lbl)


## 玩家靠近/离开时切换休息提示（与 Animal / ItemPickup 的接口一致）
func set_interact_hint(on: bool) -> void:
	var lbl := get_node_or_null("RestHint") as Label
	if lbl != null:
		lbl.visible = on


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
			# show_area_visual=false 时隐藏色块（仅做地点标记用）
			color_rect.visible = show_area_visual
			color_rect.size = size
			color_rect.position = -size * 0.5
			color_rect.color = building_color
		if house_sprite:
			house_sprite.visible = false

	# Label 跟随视觉尺寸
	if lbl:
		lbl.text = display_name
		# 标签自身 scale=0.5（2 倍字号渲染再缩回，抵消相机 zoom 的模糊），
		# 因此 size 要按缩放前的值给，position 再按缩放后的实际宽度居中。
		var lbl_scale: float = lbl.scale.x if lbl.scale.x > 0.0 else 1.0
		var raw_w: float = max(visual_size.x + 80.0, 200.0)
		lbl.size = Vector2(raw_w, 30.0)
		lbl.pivot_offset = Vector2.ZERO
		lbl.position = Vector2(
			-raw_w * lbl_scale * 0.5,
			-visual_size.y * 0.5 - 26.0
		)
		# 建筑标签：小灰字，无填充感，与 NPC 名字明显区分
		lbl.add_theme_font_size_override("font_size", 20)
		lbl.add_theme_color_override("font_color", Color(0.94, 0.94, 0.94, 0.8))
		lbl.add_theme_color_override("font_outline_color", Color(0, 0, 0, 0.75))
		lbl.add_theme_constant_override("outline_size", 5)
		lbl.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
		lbl.vertical_alignment = VERTICAL_ALIGNMENT_CENTER

	_update_entry_point()
	_update_z_index(visual_size)
	_update_collision(visual_size)


## 自动给建筑加碰撞体，避免 NPC 穿过建筑后闲逛到背面
func _update_collision(visual_size: Vector2) -> void:
	# show_area_visual=false（如广场）不要碰撞
	if not show_area_visual:
		return
	if visual_size.x <= 0 or visual_size.y <= 0:
		return
	# 已有碰撞体则跳过（用户手动放置过的不要重复加）
	for child in get_children():
		if child is StaticBody2D and child.name == "AutoCollision":
			return

	var body := StaticBody2D.new()
	body.name = "AutoCollision"
	add_child(body)
	var shape := CollisionShape2D.new()
	var rect := RectangleShape2D.new()
	# 只挡建筑下半部分（脚面），让 NPC 能从上方"经过"建筑顶部视觉
	rect.size = Vector2(visual_size.x * 0.9, visual_size.y * 0.6)
	shape.shape = rect
	shape.position = Vector2(0, visual_size.y * 0.2)  # 偏下放置
	body.add_child(shape)


func _update_z_index(visual_size: Vector2) -> void:
	z_as_relative = false
	if not show_area_visual:
		# 纯区域标记：z_index 固定在地图层以上、角色层以下，不遮挡任何人
		z_index = -80
		return
	# 有视觉的建筑：以入口点 Y 为排序基准，与 animal/player 同一公式
	var sort_y := global_position.y + entry_offset.y
	z_index = int(sort_y / 4)


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
