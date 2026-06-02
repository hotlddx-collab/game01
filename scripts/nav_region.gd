extends NavigationRegion2D
## 动态导航区域（Godot 4.6 新 API）。
##
## 从场景内 StaticBody2D（ObstacleLayer 生成）自动烘焙可行走区域。

@export var walk_min: Vector2 = Vector2(-320, -320)
@export var walk_max: Vector2 = Vector2(1600, 1120)


func _ready() -> void:
	if Engine.is_editor_hint():
		return
	# 等 ObstacleLayer 把 StaticBody2D 子节点生成完毕
	await get_tree().physics_frame
	await get_tree().physics_frame
	_bake()


func _bake() -> void:
	var nav_poly := NavigationPolygon.new()
	nav_poly.baking_rect = Rect2(walk_min, walk_max - walk_min)
	nav_poly.parsed_geometry_type = NavigationPolygon.PARSED_GEOMETRY_STATIC_COLLIDERS
	nav_poly.source_geometry_mode  = NavigationPolygon.SOURCE_GEOMETRY_ROOT_NODE_CHILDREN

	var source_geo := NavigationMeshSourceGeometryData2D.new()
	NavigationServer2D.parse_source_geometry_data(nav_poly, source_geo, get_parent())
	# 传入具名方法而非 lambda，避免 await 问题
	NavigationServer2D.bake_from_source_geometry_data(
		nav_poly, source_geo,
		_on_bake_done.bind(nav_poly)
	)


func _on_bake_done(nav_poly: NavigationPolygon) -> void:
	navigation_polygon = nav_poly
	print("[NavRegion] 导航网格烘焙完成（StaticBody2D 自动识别）")
	# deferred：确保 NavigationServer 把新 polygon 注册完再通知 NPC
	call_deferred("_notify_npcs")


func _notify_npcs() -> void:
	get_tree().call_group("npc", "_refresh_nav_target")
