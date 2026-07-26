extends Node
## Agent 后端 WebSocket 客户端（autoload）
##
## 自动连接 ws://HOST:PORT/ws，断线重试。
## 提供 request_greet() / request_chat() 异步接口，结果通过信号回传。

signal connected
signal disconnected
signal reply_received(animal_id: String, text: String)
signal npc_chat_received(speaker_id: String, listener_id: String, text: String)
signal npc_intent_received(initiator_id: String, target_id: String, intent_text: String)
signal chat_intent_made(animal_id: String, target_name: String, summary: String)
signal npc_gift_received(animal_id: String, item_id: String, item_name: String, message: String)
signal affection_changed(animal_id: String, value: int, level: String, delta: int)
signal mood_changed(animal_id: String, emote: String, level: String)
signal rumor_reply_received(info: Dictionary)
signal gift_received(animal_id: String, item_id: String, delta: int, pref: String, count_after: int)
signal quest_offer_received(animal_id: String, quest_id: String, title: String, desc: String, kind: String, give_item: String, give_count: int, target_npc: String, message_summary: String, item_id: String, required: int)
signal quest_completed_received(animal_id: String, quest_id: String, title: String, kind: String, reward_item: String, reward_count: int, consume_item: String, consume_count: int)
signal quest_progress_received(animal_id: String, quest_id: String, title: String, desc: String, progress: int, required: int)
signal election_state_received(view: Dictionary)
signal election_result_received(info: Dictionary)
signal opponent_action_received(info: Dictionary)
signal promise_state_received(info: Dictionary)
signal debate_questions_received(info: Dictionary)
signal debate_rebuttal_received(info: Dictionary)
signal debate_result_received(info: Dictionary)
signal power_state_received(info: Dictionary)
signal power_result_received(info: Dictionary)
signal crisis_state_received(info: Dictionary)
signal crisis_result_received(info: Dictionary)
signal mayor_task_state_received(info: Dictionary)
signal mayor_task_result_received(info: Dictionary)
signal day_event_received(info: Dictionary)
signal error_received(message: String)

@export var scheme: String = "ws"   # ws（本地）/ wss（云端 TLS）
@export var host: String = "127.0.0.1"
@export var port: int = 8765
@export var path: String = "/ws"
@export var auto_reconnect: bool = true
@export var reconnect_interval: float = 3.0

## 生产服务器（导出版默认连这里；编辑器里仍连本地 127.0.0.1 便于开发）。
const PROD_SCHEME := "ws"
const PROD_HOST := "106.14.139.241"
const PROD_PORT := 8765

## 运行时可用 user://server.cfg 覆盖连接地址，无需重新导出：
##   [server]
##   url="ws://1.2.3.4:8765/ws"
const _OVERRIDE_PATH := "user://server.cfg"
var _resolved_url: String = ""

## 会话隔离：每个安装生成一个持久 session_id，连接时以 ?sid= 上报，
## 服务器据此给每个玩家独立世界（各玩各的，共用同一后端 LLM）。
const _SESSION_PATH := "user://session.cfg"
var _session_id: String = ""


var _ws: WebSocketPeer = WebSocketPeer.new()
var _connected: bool = false
var _reconnect_timer: float = 0.0
var _ever_attempted: bool = false


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	_try_connect()
	# 监听游戏小时变化，22:00 起通知后端触发每日反思
	WorldClock.hour_changed.connect(_on_world_hour_changed)


func _process(delta: float) -> void:
	_ws.poll()
	var state := _ws.get_ready_state()

	match state:
		WebSocketPeer.STATE_OPEN:
			if not _connected:
				_connected = true
				print("[AgentClient] connected %s" % _resolve_url())
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
	var url := _resolve_url()
	if _ever_attempted:
		print("[AgentClient] reconnecting %s" % url)
	else:
		print("[AgentClient] connecting %s" % url)
		_ever_attempted = true
	var err := _ws.connect_to_url(url)
	if err != OK:
		push_warning("[AgentClient] connect_to_url failed err=%d" % err)


## 解析连接地址：优先读 user://server.cfg 的 [server]/url；
## 否则编辑器内连本地导出变量地址，导出版连生产服务器。
func _resolve_url() -> String:
	if _resolved_url != "":
		return _resolved_url
	var base := ""
	var cfg := ConfigFile.new()
	if cfg.load(_OVERRIDE_PATH) == OK:
		var u := String(cfg.get_value("server", "url", ""))
		if u != "":
			base = u
	if base == "":
		if OS.has_feature("editor"):
			base = "%s://%s:%d%s" % [scheme, host, port, path]
		else:
			base = "%s://%s:%d%s" % [PROD_SCHEME, PROD_HOST, PROD_PORT, path]
	# 追加会话标识：各玩家独立世界
	var sep := "&" if base.contains("?") else "?"
	_resolved_url = "%s%ssid=%s" % [base, sep, _get_session_id()]
	return _resolved_url


## 读取 / 生成持久 session_id（存 user://session.cfg），重连沿用同一 id。
func _get_session_id() -> String:
	if _session_id != "":
		return _session_id
	var cfg := ConfigFile.new()
	if cfg.load(_SESSION_PATH) == OK:
		var sid := String(cfg.get_value("session", "id", ""))
		if sid != "":
			_session_id = sid
			return _session_id
	_session_id = _gen_uuid()
	cfg.set_value("session", "id", _session_id)
	cfg.save(_SESSION_PATH)
	return _session_id


func _gen_uuid() -> String:
	# 混入高熵源再播种，避免 randi() 确定性序列导致多端撞同一 id
	seed(hash("%s|%s|%d|%d" % [
		OS.get_unique_id(),
		Time.get_datetime_string_from_system(true),
		Time.get_ticks_usec(),
		randi(),
	]))
	var bytes := PackedByteArray()
	for i in 16:
		bytes.append(randi() % 256)
	return bytes.hex_encode()



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


## 玩家送礼给 NPC（item_id 必须是 ItemDB 已知物品）
func request_gift(animal_id: String, item_id: String, context: Dictionary = {}) -> bool:
	return _send({
		"type": "gift",
		"animal_id": animal_id,
		"item_id": item_id,
		"context": context,
	})


## 八卦·打听：问 NPC 最近听说了什么
func request_rumor_inquire(animal_id: String) -> bool:
	return _send({
		"type": "rumor_inquire",
		"animal_id": animal_id,
	})


## 八卦·放话：把一句话灌给 NPC，成为新话题
func request_rumor_spread(animal_id: String, content: String, subject_id: String = "player", sentiment: String = "neutral", truth: int = 0) -> bool:
	return _send({
		"type": "rumor_spread",
		"animal_id": animal_id,
		"subject_id": subject_id,
		"content": content,
		"sentiment": sentiment,
		"truth": truth,
		"game_day": WorldClock.get_day(),
	})


## 八卦·辟谣：向 NPC 澄清某条话题
func request_rumor_debunk(animal_id: String, rumor_id: int) -> bool:
	return _send({
		"type": "rumor_debunk",
		"animal_id": animal_id,
		"rumor_id": rumor_id,
	})


## 发送游戏时间 tick，触发后端每日反思判断
func send_time_tick() -> bool:
	return _send({
		"type": "time_tick",
		"game_day": WorldClock.get_day(),
		"game_hour": WorldClock.get_hour(),
	})


## 拉取当前选举状态（HUD 启动 / 每日 06:00 调用）
func request_election_query() -> bool:
	return _send({
		"type": "election_query",
		"game_day": WorldClock.get_day(),
	})


## 调试：强制触发当前任期投票结算（用于测试投票演出，不必等 7 天）
func request_debug_force_vote() -> bool:
	return _send({
		"type": "debug_force_vote",
		"game_day": WorldClock.get_day(),
	})


## 拉取当前承诺池快照（按 P 键 / 进游戏时）
func request_promise_query() -> bool:
	return _send({
		"type": "promise_query",
		"game_day": WorldClock.get_day(),
	})


## 辩论日：拉取 3 道辩题（含 4 象限选项）
func request_debate_start() -> bool:
	return _send({
		"type": "debate_start",
		"game_day": WorldClock.get_day(),
	})


## 辩论日：玩家选完某题，请求对手即时反驳
func request_debate_rebut(question_index: int, question: String, stance: String, answer_text: String) -> bool:
	return _send({
		"type": "debate_rebut",
		"game_day": WorldClock.get_day(),
		"question_index": question_index,
		"question": question,
		"stance": stance,
		"answer_text": answer_text,
	})


## 辩论日：提交全部答案，结算辩论分。answers = {question_index(int): stance(String)}
func request_debate_submit(answers: Dictionary) -> bool:
	return _send({
		"type": "debate_submit",
		"game_day": WorldClock.get_day(),
		"answers": answers,
	})


## 权力点：拉取当前状态 + 可用行动 + 目标
func request_power_query() -> bool:
	return _send({
		"type": "power_query",
		"game_day": WorldClock.get_day(),
	})


## 权力点：执行行动（visit 需 target_id）
func request_power_action(action: String, target_id: String = "") -> bool:
	return _send({
		"type": "power_action",
		"game_day": WorldClock.get_day(),
		"action": action,
		"target_id": target_id,
	})


## 调试：把玩家设为现任并补满权力点
func request_debug_grant_power() -> bool:
	return _send({
		"type": "debug_grant_power",
		"game_day": WorldClock.get_day(),
	})


## 调试：立即触发一批对手行动（Ctrl+O，验证追赶用）
func request_debug_opponent_action() -> bool:
	return _send({
		"type": "debug_opponent_action",
		"game_day": WorldClock.get_day(),
	})


## 危机调解：查询当前危机（open=true 时后端生成双方说法，用于打开面板）
func request_crisis_query(open_detail: bool = false) -> bool:
	return _send({
		"type": "crisis_query",
		"game_day": WorldClock.get_day(),
		"open": open_detail,
	})


## 危机调解：提交调解方案
func request_crisis_resolve(crisis_id: int, option_id: String, inventory: Dictionary = {}) -> bool:
	return _send({
		"type": "crisis_resolve",
		"game_day": WorldClock.get_day(),
		"crisis_id": crisis_id,
		"option_id": option_id,
		"inventory": inventory,
	})


## 调试：立即触发一个危机（Ctrl+J）
func request_debug_spawn_crisis(template_id: String = "") -> bool:
	return _send({
		"type": "debug_spawn_crisis",
		"game_day": WorldClock.get_day(),
		"game_hour": WorldClock.get_hour(),
		"template_id": template_id,
	})


## 镇长政务任务：查询当前任务
func request_mayor_task_query() -> bool:
	return _send({
		"type": "mayor_task_query",
		"game_day": WorldClock.get_day(),
	})


## 镇长政务任务：指派某 NPC 执行（method: persuade|reason|threat）
func request_mayor_task_assign(task_id: int, executor_id: String, method: String) -> bool:
	return _send({
		"type": "mayor_task_assign",
		"game_day": WorldClock.get_day(),
		"game_hour": WorldClock.get_hour(),
		"task_id": task_id,
		"executor_id": executor_id,
		"method": method,
	})


## 调试：立即刷新一个镇务任务
func request_debug_spawn_mayor_task() -> bool:
	return _send({
		"type": "debug_spawn_mayor_task",
		"game_day": WorldClock.get_day(),
		"game_hour": WorldClock.get_hour(),
	})


## GM：让玩家直接成为现任镇长（便于测试镇务玩法）
func request_debug_make_mayor() -> bool:
	return _send({
		"type": "debug_make_mayor",
		"game_day": WorldClock.get_day(),
	})


func _on_world_hour_changed(hour: int) -> void:
	## 每个整点都通知后端：07:00 触发对手动作 / 22:00 触发反思+权重重算
	if _connected:
		send_time_tick()
	## 06:00 拉一次选举状态（保证 HUD 倒计时跨天刷新）
	if hour == 6 and _connected:
		request_election_query()


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
			# silent=true 的 reply 只用于推好感变化（如权力巡视），不进对话框
			if not bool(data.get("silent", false)):
				reply_received.emit(aid, data.get("text", ""))
			var aff = data.get("affection", null)
			if typeof(aff) == TYPE_DICTIONARY and aff.has("value"):
				affection_changed.emit(
					aid,
					int(aff.get("value", 0)),
					String(aff.get("level", "neutral")),
					int(aff.get("delta", 0)),
				)
			var mood = data.get("mood", null)
			if typeof(mood) == TYPE_DICTIONARY and mood.has("emote"):
				mood_changed.emit(
					aid,
					String(mood.get("emote", "")),
					String(mood.get("level", "calm")),
				)
			var gift = data.get("gift", null)
			if typeof(gift) == TYPE_DICTIONARY and gift.has("item_id"):
				gift_received.emit(
					aid,
					String(gift.get("item_id", "")),
					int(gift.get("delta", 0)),
					String(gift.get("pref", "neutral")),
					int(gift.get("count_after", 0)),
				)
			var intent = data.get("intent", null)
			if typeof(intent) == TYPE_DICTIONARY and intent.has("target_name"):
				chat_intent_made.emit(
					aid,
					String(intent.get("target_name", "")),
					String(intent.get("summary", "")),
				)
			var npc_gift = data.get("npc_gift", null)
			if typeof(npc_gift) == TYPE_DICTIONARY and npc_gift.has("item_id"):
				npc_gift_received.emit(
					aid,
					String(npc_gift.get("item_id", "")),
					String(npc_gift.get("item_name", "")),
					String(npc_gift.get("message", "")),
				)
			# 任务相关推送（独立于 npc_gift，无论 NPC 是否给礼物都要派）
			var qo = data.get("quest_offer", null)
			if typeof(qo) == TYPE_DICTIONARY and qo.has("quest_id"):
				quest_offer_received.emit(
					aid,
					String(qo.get("quest_id", "")),
					String(qo.get("title", "")),
					String(qo.get("desc", "")),
					String(qo.get("kind", "")),
					String(qo.get("give_item", "")),
					int(qo.get("give_count", 0)),
					String(qo.get("target_npc", "")),
					String(qo.get("message_summary", "")),
					String(qo.get("item_id", "")),
					int(qo.get("required", 0)),
				)
			var qp = data.get("quest_progress", null)
			if typeof(qp) == TYPE_DICTIONARY and qp.has("quest_id"):
				quest_progress_received.emit(
					aid,
					String(qp.get("quest_id", "")),
					String(qp.get("title", "")),
					String(qp.get("desc", "")),
					int(qp.get("progress", 0)),
					int(qp.get("required", 0)),
				)
			var qc = data.get("quest_completed", null)
			if typeof(qc) == TYPE_DICTIONARY and qc.has("quest_id"):
				quest_completed_received.emit(
					aid,
					String(qc.get("quest_id", "")),
					String(qc.get("title", "")),
					String(qc.get("kind", "")),
					String(qc.get("reward_item", "")),
					int(qc.get("reward_count", 1)),
					String(qc.get("consume_item", "")),
					int(qc.get("consume_count", 0)),
				)
		"npc_chat_reply":
			npc_chat_received.emit(
				data.get("speaker_id", ""),
				data.get("listener_id", ""),
				data.get("text", "")
			)
		"npc_intent":
			npc_intent_received.emit(
				data.get("initiator_id", ""),
				data.get("target_id", ""),
				data.get("intent_text", "")
			)
		"rumor_reply":
			rumor_reply_received.emit(data)
		"election_state":
			election_state_received.emit(data)
		"election_result":
			election_result_received.emit(data)
		"opponent_action":
			opponent_action_received.emit(data)
		"promise_state":
			promise_state_received.emit(data)
		"debate_questions":
			debate_questions_received.emit(data)
		"debate_rebuttal":
			debate_rebuttal_received.emit(data)
		"debate_result":
			debate_result_received.emit(data)
		"power_state":
			power_state_received.emit(data)
		"power_result":
			power_result_received.emit(data)
		"crisis_state":
			crisis_state_received.emit(data)
		"crisis_result":
			crisis_result_received.emit(data)
		"mayor_task_state":
			mayor_task_state_received.emit(data)
		"mayor_task_result":
			mayor_task_result_received.emit(data)
		"day_event":
			day_event_received.emit(data)
		"error":
			var m: String = data.get("message", "未知错误")
			push_warning("[AgentClient] server error: %s" % m)
			error_received.emit(m)
		"ok":
			pass
		_:
			push_warning("[AgentClient] unknown msg type: %s" % msg_type)
