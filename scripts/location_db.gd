extends Node
## 地点数据库（autoload）
##
## 加载 data/locations.json，提供地点名 → 坐标查询。
## 数据驱动：改地点位置只改 JSON，不动代码。

const LOCATIONS_PATH: String = "res://data/locations.json"

var _locations: Dictionary = {}


func _ready() -> void:
	_load_locations()


func _load_locations() -> void:
	if not FileAccess.file_exists(LOCATIONS_PATH):
		push_error("LocationDB: 找不到 %s" % LOCATIONS_PATH)
		return
	var f := FileAccess.open(LOCATIONS_PATH, FileAccess.READ)
	var text := f.get_as_text()
	f.close()
	var data = JSON.parse_string(text)
	if typeof(data) != TYPE_DICTIONARY:
		push_error("LocationDB: locations.json 格式错误")
		return
	for key in data.keys():
		var entry: Dictionary = data[key]
		_locations[key] = {
			"position": Vector2(entry.get("x", 0.0), entry.get("y", 0.0)),
			"label": entry.get("label", key),
		}


## 地点名 → Vector2 坐标，找不到返回 Vector2.ZERO
func get_pos(name: String) -> Vector2:
	if not _locations.has(name):
		push_warning("LocationDB: 未知地点 '%s'" % name)
		return Vector2.ZERO
	return _locations[name].position


## 地点名 → 显示名
func get_label(name: String) -> String:
	if not _locations.has(name):
		return name
	return _locations[name].label


## 全部地点字典副本
func all_locations() -> Dictionary:
	return _locations.duplicate(true)
