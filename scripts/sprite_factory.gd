extends Node
class_name SpriteFactory
## 角色 Spritesheet 切帧工厂
##
## 资源：BDragon1727 / 72 Cute Pixel Character
## 单帧 64×64，整图 768×1408（12 列 × 22 行）
##
## ⚠️ 重要：这套素材所有动作都是「正面朝向」单方向。
## 玩家朝哪边走都用同一个 walk 动画；朝"左"通过 flip_h = true 镜像实现。
##
## 行布局（0-indexed，已验证）：
##   Row 0   待机 idle      (4 帧)   呼吸
##   Row 1   晃动 sway      (4 帧)   idle 变体（备用）
##   Row 2   走路 walk      (6 帧)   ✅ 主用
##   Row 3   跑步 run       (6 帧)   ✅ 备用
##   Row 5   受击 hit       (4 帧)   one-shot
##   Row 6   消散 dissolve  (10 帧)
##   Row 7   消失 vanish    (11 帧)
##   Row 8   碎裂 shatter   (12 帧)
##   Row 9   出现 appear    (12 帧)  one-shot
##   Row 10  慢挥 swing_slow(8 帧)
##   Row 11  快挥 swing_fast(6 帧)
##   Row 12  穿刺 thrust    (6 帧)
##   Row 13  投掷 throw     (6 帧)
##   Row 14  射箭 shoot     (6 帧)
##   Row 16  被拉 pulled    (6 帧)
##   Row 17  拉拽 pulling   (6 帧)
##   Row 18  蹲走 crouch    (6 帧)
##   Row 19  寒冷 cold      (10 帧)
##   Row 20  中电 shocked   (12 帧)
##   Row 21  黑洞 vortex    (11 帧)
##
## 用法：
##   $AnimatedSprite2D.sprite_frames = SpriteFactory.build_frames_from_path(path)
##   $AnimatedSprite2D.play("walk")
##   # 朝左：play("walk") + flip_h = true

const FRAME_SIZE: Vector2i = Vector2i(64, 64)

# 当前接入的动画（先少接，后期按需扩展）
const ANIMATIONS: Dictionary = {
	"idle":   {"row": 0,  "frames": 4,  "fps": 4.0,  "loop": true},
	"sway":   {"row": 1,  "frames": 4,  "fps": 5.0,  "loop": true},
	"walk":   {"row": 2,  "frames": 6,  "fps": 10.0, "loop": true},
	"run":    {"row": 3,  "frames": 6,  "fps": 14.0, "loop": true},
	"hit":    {"row": 5,  "frames": 4,  "fps": 12.0, "loop": false},
	"appear": {"row": 9,  "frames": 12, "fps": 14.0, "loop": false},
	"vanish": {"row": 7,  "frames": 11, "fps": 14.0, "loop": false},
}


## 从 sheet texture 构建 SpriteFrames。
static func build_frames(texture: Texture2D) -> SpriteFrames:
	var sf := SpriteFrames.new()
	if sf.has_animation("default"):
		sf.remove_animation("default")
	for anim_name in ANIMATIONS:
		var cfg: Dictionary = ANIMATIONS[anim_name]
		sf.add_animation(anim_name)
		sf.set_animation_speed(anim_name, cfg.fps)
		sf.set_animation_loop(anim_name, cfg.loop)
		var row: int = cfg.row
		var frame_count: int = cfg.frames
		for col in range(frame_count):
			var atlas := AtlasTexture.new()
			atlas.atlas = texture
			atlas.region = Rect2(
				col * FRAME_SIZE.x,
				row * FRAME_SIZE.y,
				FRAME_SIZE.x,
				FRAME_SIZE.y
			)
			sf.add_frame(anim_name, atlas)
	return sf


## 路径 → SpriteFrames（找不到返回 null）。
## 智能加载：优先用 .tres（编辑器可见的资源），回退到运行时切 PNG。
## - 传入 .tres 路径 → 直接 load
## - 传入 .png 路径 → 先尝试同目录同名 .tres，否则切 PNG
static func build_frames_from_path(path: String) -> SpriteFrames:
	# 1. 直接是 .tres
	if path.ends_with(".tres"):
		if ResourceLoader.exists(path):
			return load(path) as SpriteFrames
		push_warning("SpriteFactory: .tres 不存在 %s" % path)
		return null

	# 2. 是 .png：先尝试同名 .tres
	var tres_path := path.get_basename() + ".tres"
	if ResourceLoader.exists(tres_path):
		var sf := load(tres_path) as SpriteFrames
		if sf != null:
			return sf

	# 3. 回退：运行时切 PNG（保底，编辑器看不到）
	if not ResourceLoader.exists(path):
		push_warning("SpriteFactory: 找不到 %s" % path)
		return null
	var tex := load(path) as Texture2D
	if tex == null:
		push_warning("SpriteFactory: 加载失败 %s" % path)
		return null
	push_warning("SpriteFactory: 用运行时切帧（建议运行 tools/build_sprite_frames.gd 生成 .tres）")
	return build_frames(tex)


## 工具：根据移动向量算朝向字符串（"down"/"up"/"left"/"right"）
## 静止返回 ""
static func direction_from_velocity(velocity: Vector2, threshold: float = 5.0) -> String:
	if velocity.length() < threshold:
		return ""
	if abs(velocity.x) > abs(velocity.y):
		return "right" if velocity.x > 0 else "left"
	else:
		return "down" if velocity.y > 0 else "up"


## 工具：朝向 → 是否需要 flip_h（仅"左"需翻转）
static func direction_needs_flip(dir: String) -> bool:
	return dir == "left"
