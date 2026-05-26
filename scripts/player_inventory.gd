extends Node
## 玩家库存（autoload）
##
## 简单 Dict：{item_id: count}。
## 会触发 inventory_changed 信号通知 UI 刷新。

signal inventory_changed
signal item_added(item_id: String, count: int)

var _items: Dictionary = {}  # item_id → int


func add_item(item_id: String, count: int = 1) -> void:
	if count <= 0:
		return
	if not ItemDB.has(item_id):
		push_warning("[PlayerInventory] 未知 item_id: %s" % item_id)
		return
	var cur: int = _items.get(item_id, 0)
	_items[item_id] = cur + count
	item_added.emit(item_id, count)
	inventory_changed.emit()


func remove_item(item_id: String, count: int = 1) -> bool:
	var cur: int = _items.get(item_id, 0)
	if cur < count:
		return false
	var new_count: int = cur - count
	if new_count == 0:
		_items.erase(item_id)
	else:
		_items[item_id] = new_count
	inventory_changed.emit()
	return true


func has_item(item_id: String) -> bool:
	return _items.get(item_id, 0) > 0


func get_count(item_id: String) -> int:
	return _items.get(item_id, 0)


func get_all() -> Dictionary:
	return _items.duplicate()


func is_empty() -> bool:
	return _items.is_empty()


func total_count() -> int:
	var n: int = 0
	for c in _items.values():
		n += int(c)
	return n
