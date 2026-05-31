extends Node
## 位置数据库（autoload）
##
## Building 节点自行 register()，非建筑地点用 register_pos() 手动注册。
## 非建筑地点坐标写在 data/world/locations.json。

var _buildings: Dictionary = {}   # building_id → Building 节点
var _positions: Dictionary = {}   # id → Vector2（非建筑地点）


func _ready() -> void:
	_load_locations_json()


## 从 data/world/locations.json 加载非建筑地点坐标
func _load_locations_json() -> void:
	const PATH := "res://data/world/locations.json"
	if not FileAccess.file_exists(PATH):
		return
	var f := FileAccess.open(PATH, FileAccess.READ)
	if f == null:
		return
	var data = JSON.parse_string(f.get_as_text())
	f.close()
	if data == null or not data is Dictionary:
		return
	for id in data:
		var v = data[id]
		if v is Array and v.size() >= 2:
			_positions[id] = Vector2(float(v[0]), float(v[1]))


## 手动注册一个坐标（供脚本调用）
func register_pos(id: String, pos: Vector2) -> void:
	_positions[id] = pos


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
	if _buildings.has(name):
		return _buildings[name].get_entry_position()
	if _positions.has(name):
		return _positions[name]
	push_warning("LocationDB: 未知地点 '%s'（已注册建筑：%s，坐标：%s）" % [
		name, _buildings.keys(), _positions.keys()])
	return Vector2.ZERO


## 地点 id → 显示名（中文）
func get_label(name: String) -> String:
	var b = _buildings.get(name)
	if b != null:
		return b.display_name
	return name


## 全部地点字典副本：{ id: { position, label } }
func all_locations() -> Dictionary:
	var result := {}
	for id in _buildings:
		result[id] = {
			"position": _buildings[id].get_entry_position(),
			"label": _buildings[id].display_name,
		}
	for id in _positions:
		if not result.has(id):
			result[id] = {"position": _positions[id], "label": id}
	return result
