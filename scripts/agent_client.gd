extends Node
## Agent 后端 WebSocket 客户端（autoload）
##
## 自动连接 ws://HOST:PORT/ws，断线重试。
## 提供 request_greet() / request_chat() 异步接口，结果通过信号回传。

signal connected
signal disconnected
signal reply_received(animal_id: String, text: String)
signal npc_chat_received(speaker_id: String, listener_id: String, text: String)
signal affection_changed(animal_id: String, value: int, level: String, delta: int)
signal error_received(message: String)

@export var host: String = "127.0.0.1"
@export var port: int = 8765
@export var path: String = "/ws"
@export var auto_reconnect: bool = true
@export var reconnect_interval: float = 3.0

var _ws: WebSocketPeer = WebSocketPeer.new()
var _connected: bool = false
var _reconnect_timer: float = 0.0
var _ever_attempted: bool = false


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	_try_connect()


func _process(delta: float) -> void:
	_ws.poll()
	var state := _ws.get_ready_state()

	match state:
		WebSocketPeer.STATE_OPEN:
			if not _connected:
				_connected = true
				print("[AgentClient] connected ws://%s:%d%s" % [host, port, path])
				connected.emit()
			# 收包
			while _ws.get_available_packet_count() > 0:
				var pkt: PackedByteArray = _ws.get_packet()
				_handle_packet(pkt.get_string_from_utf8())

		WebSocketPeer.STATE_CLOSED:
			if _connected:
				_connected = false
				disconnected.emit()
				push_warning("[AgentClient] disconnected (code=%d reason=%s)" % [_ws.get_close_code(), _ws.get_close_reason()])
			if auto_reconnect:
				_reconnect_timer += delta
				if _reconnect_timer >= reconnect_interval:
					_reconnect_timer = 0.0
					_try_connect()

		WebSocketPeer.STATE_CONNECTING, WebSocketPeer.STATE_CLOSING:
			pass


func _try_connect() -> void:
	var url := "ws://%s:%d%s" % [host, port, path]
	if _ever_attempted:
		print("[AgentClient] reconnecting %s" % url)
	else:
		print("[AgentClient] connecting %s" % url)
		_ever_attempted = true
	var err := _ws.connect_to_url(url)
	if err != OK:
		push_warning("[AgentClient] connect_to_url failed err=%d" % err)


func is_connected_to_server() -> bool:
	return _connected


# ---------- 公共接口 ----------

func request_greet(animal_id: String, context: Dictionary = {}) -> bool:
	return _send({
		"type": "greet",
		"animal_id": animal_id,
		"context": context,
	})


func request_chat(animal_id: String, user_text: String, context: Dictionary = {}) -> bool:
	return _send({
		"type": "chat",
		"animal_id": animal_id,
		"user_text": user_text,
		"context": context,
	})


func request_reset(animal_id: String) -> bool:
	return _send({
		"type": "reset",
		"animal_id": animal_id,
	})


## 触发 NPC↔NPC 对话（speaker 主动跟 listener 说一句）
func request_npc_chat(speaker_id: String, listener_id: String, context: Dictionary = {}) -> bool:
	return _send({
		"type": "npc_chat",
		"speaker_id": speaker_id,
		"listener_id": listener_id,
		"context": context,
	})


## 玩家偷听到 NPC 对话，通知后端写双方记忆 + 世界事件
func request_eavesdrop(speaker_id: String, listener_id: String, text: String, context: Dictionary = {}) -> bool:
	return _send({
		"type": "eavesdrop",
		"speaker_id": speaker_id,
		"listener_id": listener_id,
		"text": text,
		"context": context,
	})


# ---------- 内部 ----------

func _send(payload: Dictionary) -> bool:
	if not _connected:
		push_warning("[AgentClient] not connected, drop payload")
		return false
	var text := JSON.stringify(payload)
	var err := _ws.send_text(text)
	if err != OK:
		push_warning("[AgentClient] send err=%d" % err)
		return false
	return true


func _handle_packet(text: String) -> void:
	var data = JSON.parse_string(text)
	if typeof(data) != TYPE_DICTIONARY:
		push_warning("[AgentClient] bad packet: %s" % text)
		return
	var msg_type: String = data.get("type", "")
	match msg_type:
		"reply":
			var aid: String = data.get("animal_id", "")
			reply_received.emit(aid, data.get("text", ""))
			var aff = data.get("affection", null)
			if typeof(aff) == TYPE_DICTIONARY and aff.has("value"):
				affection_changed.emit(
					aid,
					int(aff.get("value", 0)),
					String(aff.get("level", "neutral")),
					int(aff.get("delta", 0)),
				)
		"npc_chat_reply":
			npc_chat_received.emit(
				data.get("speaker_id", ""),
				data.get("listener_id", ""),
				data.get("text", "")
			)
		"error":
			var m: String = data.get("message", "未知错误")
			push_warning("[AgentClient] server error: %s" % m)
			error_received.emit(m)
		"ok":
			pass
		_:
			push_warning("[AgentClient] unknown msg type: %s" % msg_type)
