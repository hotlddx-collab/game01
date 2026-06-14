extends CanvasLayer
## 背包界面（layer=5）
##
## 两种模式：
##   浏览模式：按 I 打开，只看
##   送礼模式：对话中点"送礼"按钮打开，点物品 → 送出

signal gift_item_chosen(item_id: String)   ## 送礼模式下选中物品

@onready var panel: Panel = %Panel
@onready var item_grid: GridContainer = %ItemGrid
@onready var empty_label: Label = %EmptyLabel
@onready var title_label: Label = %Title
@onready var close_btn: Button = %CloseBtn
@onready var confirm_panel: Panel = %ConfirmPanel
@onready var confirm_label: Label = %ConfirmLabel
@onready var item_icon: TextureRect = %ItemIcon
@onready var item_name_label: Label = %ItemNameLabel
@onready var btn_ok: Button = %BtnOK
@onready var btn_cancel: Button = %BtnCancel

var _pending_item_id: String = ""

var _is_open: bool = false
var _gift_mode: bool = false
var _gift_animal_id: String = ""

const SLOT_MIN_SIZE := Vector2(80, 80)


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	panel.visible = false
	PlayerInventory.inventory_changed.connect(_on_inventory_changed)
	close_btn.pressed.connect(close)
	btn_ok.pressed.connect(_on_confirm_ok)
	btn_cancel.pressed.connect(_on_confirm_cancel)
	_style_confirm_panel()


func _style_confirm_panel() -> void:
	## ConfirmPanel：游戏风格暖棕色，和主面板统一
	var bg := StyleBoxFlat.new()
	bg.bg_color = Color(0.18, 0.13, 0.09, 0.98)
	bg.border_width_left = 3
	bg.border_width_top = 3
	bg.border_width_right = 3
	bg.border_width_bottom = 3
	bg.border_color = Color(0.75, 0.5, 0.2, 1.0)
	bg.corner_radius_top_left = 10
	bg.corner_radius_top_right = 10
	bg.corner_radius_bottom_right = 10
	bg.corner_radius_bottom_left = 10
	confirm_panel.add_theme_stylebox_override("panel", bg)

	## BtnOK：暖绿
	_style_btn(btn_ok, Color(0.25, 0.55, 0.25, 1), Color(0.3, 0.7, 0.3, 1), Color(0.9, 1.0, 0.9, 1))
	## BtnCancel：灰棕
	_style_btn(btn_cancel, Color(0.35, 0.28, 0.2, 1), Color(0.5, 0.4, 0.28, 1), Color(0.85, 0.8, 0.7, 1))


func _style_btn(btn: Button, bg: Color, border: Color, text_color: Color) -> void:
	var s := StyleBoxFlat.new()
	s.bg_color = bg
	s.border_width_left = 2
	s.border_width_top = 2
	s.border_width_right = 2
	s.border_width_bottom = 2
	s.border_color = border
	s.corner_radius_top_left = 6
	s.corner_radius_top_right = 6
	s.corner_radius_bottom_right = 6
	s.corner_radius_bottom_left = 6
	s.content_margin_left = 10
	s.content_margin_right = 10
	btn.add_theme_stylebox_override("normal", s)
	btn.add_theme_stylebox_override("hover", s)
	btn.add_theme_stylebox_override("pressed", s)
	btn.add_theme_color_override("font_color", text_color)
	btn.add_theme_font_size_override("font_size", 14)


func _input(event: InputEvent) -> void:
	if not event is InputEventKey or not event.pressed:
		return
	var ke := event as InputEventKey
	# 打字中（输入框聚焦）→ 让位，避免 I 等键误触
	var f := get_viewport().gui_get_focus_owner()
	if f is LineEdit or f is TextEdit:
		return
	if _is_open:
		# 已打开：Esc / I 关闭
		if ke.keycode == KEY_ESCAPE or ke.keycode == KEY_I:
			close()
			get_viewport().set_input_as_handled()
	else:
		# 未打开：I 打开（浏览模式）
		if ke.keycode == KEY_I:
			open()
			get_viewport().set_input_as_handled()


## 浏览模式打开
func open() -> void:
	_gift_mode = false
	_gift_animal_id = ""
	title_label.text = "🎒 背包"
	_refresh()
	panel.visible = true
	_is_open = true


## 送礼模式打开（对话中点送礼触发）
func open_for_gift(animal_id: String) -> void:
	_gift_mode = true
	_gift_animal_id = animal_id
	title_label.text = "🎁 选择要送出的礼物"
	_refresh()
	panel.visible = true
	_is_open = true


func close() -> void:
	panel.visible = false
	_is_open = false
	_gift_mode = false
	_gift_animal_id = ""
	_pending_item_id = ""
	confirm_panel.visible = false


func is_open() -> bool:
	return _is_open


func _on_inventory_changed() -> void:
	if _is_open:
		_refresh()


func _refresh() -> void:
	for child in item_grid.get_children():
		child.queue_free()

	var inv: Dictionary = PlayerInventory.get_all()
	if inv.is_empty():
		empty_label.visible = true
		item_grid.visible = false
		return

	empty_label.visible = false
	item_grid.visible = true

	for item_id in inv.keys():
		var count: int = int(inv[item_id])
		if count <= 0:
			continue
		var slot := _make_slot(item_id, count)
		item_grid.add_child(slot)


func _make_slot(item_id: String, count: int) -> PanelContainer:
	var def: Dictionary = ItemDB.get_def(item_id)
	var item_name: String = def.get("name", item_id)
	var desc: String = def.get("desc", "")
	var base_value: int = int(def.get("base_value", 0))

	var slot := PanelContainer.new()
	slot.custom_minimum_size = SLOT_MIN_SIZE
	slot.tooltip_text = "%s\n%s\n基础价值 +%d" % [item_name, desc, base_value]

	var style := StyleBoxFlat.new()
	if _gift_mode:
		# 送礼模式：暖橙色调，可点击感
		style.bg_color = Color(0.28, 0.2, 0.1, 1.0)
		style.border_color = Color(0.8, 0.55, 0.2, 0.9)
	else:
		# 浏览模式：冷灰
		style.bg_color = Color(0.22, 0.18, 0.13, 1.0)
		style.border_color = Color(0.5, 0.4, 0.25, 0.6)
	style.border_width_left = 1
	style.border_width_top = 1
	style.border_width_right = 1
	style.border_width_bottom = 1
	style.corner_radius_top_left = 4
	style.corner_radius_top_right = 4
	style.corner_radius_bottom_right = 4
	style.corner_radius_bottom_left = 4
	slot.add_theme_stylebox_override("panel", style)

	var vbox := VBoxContainer.new()
	vbox.alignment = BoxContainer.ALIGNMENT_CENTER
	slot.add_child(vbox)

	var icon_tex: Texture2D = ItemDB.get_icon(item_id)
	if icon_tex:
		var icon := TextureRect.new()
		icon.texture = icon_tex
		icon.custom_minimum_size = Vector2(32, 32)
		icon.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
		icon.size_flags_horizontal = Control.SIZE_SHRINK_CENTER
		vbox.add_child(icon)

	var name_lbl := Label.new()
	name_lbl.text = item_name
	name_lbl.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	name_lbl.add_theme_font_size_override("font_size", 11)
	name_lbl.add_theme_color_override("font_color", Color(0.9, 0.85, 0.7, 1))
	vbox.add_child(name_lbl)

	var count_lbl := Label.new()
	count_lbl.text = "x%d" % count
	count_lbl.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	count_lbl.add_theme_font_size_override("font_size", 10)
	count_lbl.add_theme_color_override("font_color", Color(0.7, 0.65, 0.5, 0.9))
	vbox.add_child(count_lbl)

	# 送礼模式：格子可点击（透明 Button 覆盖）
	if _gift_mode:
		var btn := Button.new()
		btn.flat = true
		btn.focus_mode = Control.FOCUS_NONE
		btn.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
		slot.add_child(btn)
		btn.pressed.connect(_on_gift_slot_pressed.bind(item_id))

	return slot


func _on_gift_slot_pressed(item_id: String) -> void:
	if not PlayerInventory.has_item(item_id):
		return
	_pending_item_id = item_id
	var item_name: String = ItemDB.get_item_name(item_id)
	# 更新图标 + 名称
	item_icon.texture = ItemDB.get_icon(item_id)
	item_name_label.text = item_name
	confirm_label.text = "确认送出给对方？"
	confirm_panel.visible = true


func _on_confirm_ok() -> void:
	confirm_panel.visible = false
	if _pending_item_id == "" or not PlayerInventory.has_item(_pending_item_id):
		return
	var item_id := _pending_item_id
	_pending_item_id = ""
	close()
	gift_item_chosen.emit(item_id)


func _on_confirm_cancel() -> void:
	confirm_panel.visible = false
	_pending_item_id = ""
