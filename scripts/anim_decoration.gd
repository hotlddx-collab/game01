extends Sprite2D
class_name AnimDecoration
## 通用装饰物动画
##
## 两种模式：
##   SHEET：横向 SpriteSheet 切片逐帧动画（如 Flower, Flag, Waterfall）
##   SWAP：两张贴图轮流（保留为兼容，当前主用 SHEET）

enum Mode { SHEET, SWAP }

@export var mode: Mode = Mode.SHEET
## SHEET 模式
@export var sheet: Texture2D
@export var frame_w: int = 16
@export var frame_h: int = 16
## SWAP 模式
@export var tex_a: Texture2D
@export var tex_b: Texture2D
## 通用
@export var fps: float = 4.0
@export var randomize_phase: bool = true

var _t: float = 0.0
var _idx: int = 0
var _count: int = 1
var _atlas: AtlasTexture


func _ready() -> void:
	centered = false
	if randomize_phase and fps > 0.0:
		_t = randf() * (1.0 / fps)
	match mode:
		Mode.SHEET:
			if sheet != null:
				_atlas = AtlasTexture.new()
				_atlas.atlas = sheet
				_atlas.region = Rect2(0, 0, frame_w, frame_h)
				_count = max(1, int(sheet.get_width() / frame_w))
				texture = _atlas
		Mode.SWAP:
			if tex_a != null:
				texture = tex_a
				_count = 2 if tex_b != null else 1


func _process(delta: float) -> void:
	if _count <= 1 or fps <= 0.0:
		return
	_t += delta
	if _t < 1.0 / fps:
		return
	_t = 0.0
	_idx = (_idx + 1) % _count
	match mode:
		Mode.SHEET:
			if _atlas:
				_atlas.region.position.x = _idx * frame_w
		Mode.SWAP:
			texture = tex_a if _idx == 0 else tex_b
