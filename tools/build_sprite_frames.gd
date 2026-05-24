@tool
extends EditorScript
## 批量生成 SpriteFrames .tres 文件
##
## 使用方法：
##   1. 在 Godot 编辑器里打开本脚本（双击）
##   2. 菜单 文件 → 运行（或 Ctrl+Shift+X）
##   3. 等待输出"生成完成"
##   4. 刷新 FileSystem 面板看到新生成的 .tres
##
## 行布局来自 SpriteFactory.ANIMATIONS（中央定义，单点修改）。
## 加新角色：在 SHEETS 列表加一行，重跑本脚本。

const FRAME_SIZE: Vector2i = Vector2i(64, 64)

# 要生成的角色清单：[源 PNG, 目标 .tres]
const SHEETS: Array = [
	["res://assets/characters/player.png",      "res://assets/characters/player.tres"],
	["res://assets/characters/bear_baker.png",  "res://assets/characters/bear_baker.tres"],
	["res://assets/characters/fox_postman.png", "res://assets/characters/fox_postman.tres"],
]


func _run() -> void:
	print("[BuildSpriteFrames] 开始生成...")
	var success := 0
	var fail := 0
	for entry in SHEETS:
		var src: String = entry[0]
		var dst: String = entry[1]
		if _build_one(src, dst):
			success += 1
		else:
			fail += 1
	print("[BuildSpriteFrames] 完成 — 成功 %d / 失败 %d" % [success, fail])
	# 刷新文件系统让编辑器认到新文件
	var fs := EditorInterface.get_resource_filesystem()
	if fs:
		fs.scan()


func _build_one(src_png: String, dst_tres: String) -> bool:
	if not ResourceLoader.exists(src_png):
		push_error("[BuildSpriteFrames] 源文件不存在: %s" % src_png)
		return false
	var tex := load(src_png) as Texture2D
	if tex == null:
		push_error("[BuildSpriteFrames] 加载 PNG 失败: %s" % src_png)
		return false

	var sf := SpriteFrames.new()
	if sf.has_animation("default"):
		sf.remove_animation("default")

	for anim_name in SpriteFactory.ANIMATIONS:
		var cfg: Dictionary = SpriteFactory.ANIMATIONS[anim_name]
		sf.add_animation(anim_name)
		sf.set_animation_speed(anim_name, cfg.fps)
		sf.set_animation_loop(anim_name, cfg.loop)
		var row: int = cfg.row
		var frame_count: int = cfg.frames
		for col in range(frame_count):
			var atlas := AtlasTexture.new()
			atlas.atlas = tex
			atlas.region = Rect2(
				col * FRAME_SIZE.x,
				row * FRAME_SIZE.y,
				FRAME_SIZE.x,
				FRAME_SIZE.y
			)
			sf.add_frame(anim_name, atlas)

	var err := ResourceSaver.save(sf, dst_tres)
	if err != OK:
		push_error("[BuildSpriteFrames] 保存失败 %s err=%d" % [dst_tres, err])
		return false
	print("  ✅ %s → %s" % [src_png.get_file(), dst_tres.get_file()])
	return true
