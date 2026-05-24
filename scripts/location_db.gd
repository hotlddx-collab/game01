extends Node
## 位置数据库（autoload）
##
## 由场景内 Building 节点自行调用 register() 加入。
## 不再依赖 data/locations.json（已废弃）。

var _buildings: Dictionary = {}  # building_id → Building 节点


## Building._ready 时自调用
func register(building: Node) -> void:
	if not building.has_method("get_entry_position"):
		push_warning("LocationDB: 节点 %s 没有 get_entry_position 接口" % building.name)
		return
	var id: String = building.building_id
	if id == "":
		push_warning("LocationDB: 建筑节点 %s 未设置 building_id" % building.name)
		return
	if _buildings.has(id) and _buildings[id] != building:
		push_warning("LocationDB: building_id 冲突 '%s'，覆盖" % id)
	_buildings[id] = building


## Building._exit_tree 时自调用
func unregister(building: Node) -> void:
	var id: String = building.building_id
	if _buildings.get(id) == building:
		_buildings.erase(id)


## 地点 id → 全局坐标，未注册返回 Vector2.ZERO
func get_pos(name: String) -> Vector2:
	var b = _buildings.get(name)
	if b == null:
		push_warning("LocationDB: 未知地点 '%s'（已注册：%s）" % [name, _buildings.keys()])
		return Vector2.ZERO
	return b.get_entry_position()


## 地点 id → 显示名（中文）
func get_label(name: String) -> String:
	var b = _buildings.get(name)
	if b == null:
		return name
	return b.display_name


## 全部地点字典副本：{ id: { position, label } }
func all_locations() -> Dictionary:
	var result := {}
	for id in _buildings:
		var b = _buildings[id]
		result[id] = {
			"position": b.get_entry_position(),
			"label": b.display_name,
		}
	return result
