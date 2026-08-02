extends Node2D
## 主场景控制器
##
## 流程：
##   玩家 E → 打开对话框 → 请求后端 greet → NPC 开口
##   玩家输入回车 → 请求后端 chat → NPC 回应
##   后端断开 → 显示提示，仍可关闭对话
##   玩家走远（> auto_close_distance）→ 自动关闭对话

@export var auto_close_distance: float = 130.0

@onready var player: CharacterBody2D = %Player
@onready var dialog_ui: CanvasLayer = %DialogUI
@onready var quest_hud: CanvasLayer = get_node_or_null("%QuestHUD")
@onready var crisis_panel: CanvasLayer = get_node_or_null("CrisisPanel")

var _current_animal: Animal = null
var _pending_mayor: Dictionary = {}   # executor_id -> 结算 info，等对话关闭后开演
var _situation: Dictionary = {}       # 当前镇务情境：{task_id,type,markers,spots,target_id}
var _performing: bool = false         # 正在表演，期间忽略情境拆除推送
var _resting: bool = false            # 正在播休息演出，避免重复触发
var _pending_rotation: Dictionary = {} # 收到但未应用的换届轮换信息，等黑幕过渡时才落地


func _ready() -> void:
	if player == null:
		push_error("Main: 找不到 Player 节点")
		return
	player.interact_pressed.connect(_on_player_interact)

	# 对话框信号
	dialog_ui.chat_send_requested.connect(_on_chat_send)
	dialog_ui.gift_send_requested.connect(_on_gift_send)
	dialog_ui.dialog_finished.connect(_on_dialog_finished)

	# 后端信号
	AgentClient.reply_received.connect(_on_reply_received)
	AgentClient.affection_changed.connect(_on_affection_changed)
	AgentClient.mood_changed.connect(_on_mood_changed)
	AgentClient.error_received.connect(_on_error_received)
	AgentClient.npc_intent_received.connect(_on_npc_intent)
	AgentClient.npc_gift_received.connect(_on_npc_gift)
	AgentClient.canvass_pitch_received.connect(_on_canvass_pitch)
	AgentClient.quest_offer_received.connect(_on_quest_offer)
	AgentClient.quest_progress_received.connect(_on_quest_progress)
	AgentClient.quest_completed_received.connect(_on_quest_completed)
	AgentClient.opponent_action_received.connect(_on_opponent_action)
	AgentClient.election_result_received.connect(_on_election_result)
	AgentClient.observation_clue.connect(_on_observation_clue)
	AgentClient.roster_received.connect(_on_roster_received)
	AgentClient.npc_rotation_received.connect(_on_npc_rotation)
	var election_hud := get_node_or_null("ElectionHUD")
	if election_hud and election_hud.has_signal("term_settled"):
		election_hud.term_settled.connect(_on_term_settled)
	if AgentClient.has_signal("mayor_task_result_received"):
		AgentClient.mayor_task_result_received.connect(_on_mayor_task_result)
	if AgentClient.has_signal("mayor_task_state_received"):
		AgentClient.mayor_task_state_received.connect(_on_mayor_task_state)

	AudioManager.play_game_bgm()

	_show_intro()


## 开局黑幕：介绍"你是谁/在哪/要干什么"，同时把 NPC 尚未生成的空档盖住——
## 顺便解决了"NPC 名字还没从后端刷回来就被玩家看到英文 id"的闪一下问题。
## 黑幕至少停留 3 秒（够读完），且不会早于 NPC 名单落地就提前撤下；
## 4 秒内后端仍没给名单则按兜底名单生成，保证单机可玩。
const _INTRO_TEXT := "[center]🌲 [b]欢迎来到怪物森林[/b]\n\n这里住满了外形各异、却心地善良的怪物居民。\n\n你是一位偶然闯入森林的旅人。\n镇上正在举行[b]首届镇长竞选[/b]——\n串门聊天、送礼交心、辩论造势，都能为你赢得选票。\n\n击败对手，成为森林的第一任镇长吧。[/center]"


func _show_intro() -> void:
	player.input_enabled = false

	var layer := CanvasLayer.new()
	layer.layer = 100
	add_child(layer)

	var bg := ColorRect.new()
	bg.color = Color(0, 0, 0, 1)
	bg.mouse_filter = Control.MOUSE_FILTER_STOP
	bg.set_anchors_preset(Control.PRESET_FULL_RECT)
	layer.add_child(bg)

	var text := RichTextLabel.new()
	text.bbcode_enabled = true
	text.fit_content = true
	text.scroll_active = false
	text.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	text.mouse_filter = Control.MOUSE_FILTER_IGNORE
	text.custom_minimum_size = Vector2(640, 0)
	text.set_anchors_preset(Control.PRESET_CENTER)
	text.position -= text.custom_minimum_size * 0.5
	text.add_theme_font_size_override("normal_font_size", 22)
	text.add_theme_color_override("default_color", Color(0.93, 0.93, 0.88))
	text.text = _INTRO_TEXT
	layer.add_child(text)

	var waited := 0.0
	while waited < 4.0 and not _roster_built:
		await get_tree().create_timer(0.1).timeout
		waited += 0.1
	if not _roster_built:
		_spawn_npcs_fallback()

	var remain: float = max(0.0, 3.0 - waited)
	if remain > 0.0:
		await get_tree().create_timer(remain).timeout

	var tw := create_tween()
	tw.tween_property(bg, "color:a", 0.0, 0.7)
	tw.parallel().tween_property(text, "modulate:a", 0.0, 0.5)
	await tw.finished
	layer.queue_free()
	player.input_enabled = true


func _process(_delta: float) -> void:
	# 对话期间，玩家走远 → 自动关闭
	if _current_animal == null or not dialog_ui.is_open():
		return
	if not is_instance_valid(_current_animal):
		dialog_ui.close()
		return
	var d: float = player.global_position.distance_to(_current_animal.global_position)
	if d > auto_close_distance:
		dialog_ui.close()


# ---------- 玩家交互 ----------

func _on_player_interact(target: Node) -> void:
	if dialog_ui.is_open():
		return
	# 拾取物品
	if target.is_in_group("pickup") and target.has_method("pickup"):
		var picked_item: String = target.item_id if "item_id" in target else ""
		target.pickup()
		if picked_item != "" and AgentClient.is_connected_to_server():
			AgentClient.report_pickup("player", picked_item)
		return
	# 回家休息
	if target.is_in_group("rest"):
		_do_rest()
		return
	if not (target is Animal):
		return
	var animal: Animal = target
	# 该 NPC 是当前危机当事人 → 打开调解面板（优先于普通对话/busy 判断）
	if crisis_panel and crisis_panel.has_method("is_party") and crisis_panel.is_party(animal.animal_id):
		crisis_panel.open_panel()
		return
	# 对方正在和别人交谈：拒绝开始对话
	if animal.is_busy():
		return
	_current_animal = animal

	# 锁玩家输入，避免打字时角色乱跑
	player.input_enabled = false

	# NPC 进入"和玩家对话"状态：停步 + 朝向玩家
	animal.set_busy(animal.BusyState.TALKING_PLAYER)
	animal.face_to(player.global_position)

	dialog_ui.open_chat(animal.animal_id, animal.animal_name)

	if not AgentClient.is_connected_to_server():
		dialog_ui.set_status("（未连后端）")
		dialog_ui.show_npc_line("……（这个动物似乎没有灵魂。请先启动 agent_server）")
		return

	dialog_ui.set_status("正在思考...")
	_track_talked_to(animal.animal_id)
	# 玩家也访问到了 NPC 当前所在地点
	_track_visit(animal.get_target_location())
	AgentClient.request_greet(animal.animal_id, _build_context(animal))


## 回家休息：黑屏淡入（进屋）→ 停顿 → 淡出（出门），日体力与冲刺条都补满
func _do_rest() -> void:
	if _resting:
		return
	_resting = true
	player.input_enabled = false

	var layer := CanvasLayer.new()
	layer.layer = 90
	add_child(layer)

	var fade := ColorRect.new()
	fade.color = Color(0, 0, 0, 0)
	fade.mouse_filter = Control.MOUSE_FILTER_IGNORE
	fade.set_anchors_preset(Control.PRESET_FULL_RECT)
	layer.add_child(fade)

	var tip := Label.new()
	tip.text = "🛏  休息中……"
	tip.modulate.a = 0.0
	tip.mouse_filter = Control.MOUSE_FILTER_IGNORE
	tip.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	tip.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	tip.set_anchors_preset(Control.PRESET_FULL_RECT)
	tip.add_theme_font_size_override("font_size", 28)
	tip.add_theme_color_override("font_color", Color(1, 0.95, 0.8, 1))
	layer.add_child(tip)

	var tw := create_tween()
	tw.tween_property(fade, "color:a", 1.0, 0.5)
	tw.parallel().tween_property(tip, "modulate:a", 1.0, 0.6)
	await tw.finished

	# 屋里待一会儿，再结算体力
	await get_tree().create_timer(1.1).timeout
	if player.has_method("rest"):
		player.rest()
	# 睡一觉总要花点时间——游戏时间跳 1 小时，顺带在提示上给个反馈，
	# 不然玩家会觉得"睡了但啥也没变"
	WorldClock.advance_by_minutes(60)
	tip.text = "🛏  精神满满！体力已回满\n🕐 %s" % WorldClock.format_time()

	await get_tree().create_timer(0.9).timeout
	var tw2 := create_tween()
	tw2.tween_property(tip, "modulate:a", 0.0, 0.35)
	tw2.parallel().tween_property(fade, "color:a", 0.0, 0.6)
	await tw2.finished

	layer.queue_free()
	player.input_enabled = true
	_resting = false


## 换届黑幕过渡：唱票演出播完后触发。黑幕上交代"当选/离镇/迁入"三件事，
## 期间悄悄把 NPC 换人（_apply_rotation），再把时间跳到次日早晨——
## 相当于"当晚大家都去睡了"，玩家按休息结算（含冲刺体力）。
func _on_term_settled(info: Dictionary, next_opponent_id: String) -> void:
	_do_term_transition(info, next_opponent_id)


func _do_term_transition(info: Dictionary, next_opponent_id: String) -> void:
	if _resting:
		return
	_resting = true
	player.input_enabled = false

	var layer := CanvasLayer.new()
	layer.layer = 95
	add_child(layer)

	var fade := ColorRect.new()
	fade.color = Color(0, 0, 0, 0)
	fade.mouse_filter = Control.MOUSE_FILTER_IGNORE
	fade.set_anchors_preset(Control.PRESET_FULL_RECT)
	layer.add_child(fade)

	var text := RichTextLabel.new()
	text.bbcode_enabled = true
	text.fit_content = true
	text.scroll_active = false
	text.modulate.a = 0.0
	text.mouse_filter = Control.MOUSE_FILTER_IGNORE
	text.set_anchors_preset(Control.PRESET_FULL_RECT)
	text.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	text.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	text.add_theme_font_size_override("normal_font_size", 24)
	layer.add_child(text)

	text.text = _build_term_transition_text(info, next_opponent_id)

	var tw := create_tween()
	tw.tween_property(fade, "color:a", 1.0, 0.6)
	tw.parallel().tween_property(text, "modulate:a", 1.0, 0.9)
	await tw.finished

	await get_tree().create_timer(3.0).timeout

	# 黑幕之下悄悄落地：换人、门牌、公共设施
	if not _pending_rotation.is_empty():
		_apply_rotation(_pending_rotation)
		_pending_rotation = {}

	# 整晚直接跳过——大家都去睡了，直接天亮；体力按休息结算（含冲刺条）
	if has_node("/root/WorldClock"):
		WorldClock.skip_to_next_morning()
	if player.has_method("rest"):
		player.rest()

	await get_tree().create_timer(0.5).timeout

	var tw2 := create_tween()
	tw2.tween_property(text, "modulate:a", 0.0, 0.35)
	tw2.parallel().tween_property(fade, "color:a", 0.0, 0.6)
	await tw2.finished

	layer.queue_free()
	player.input_enabled = true
	_resting = false


## 换届黑幕文案：当选/落选 + 离镇 + 迁入 + 设施停业/重开 + 下任对手，一次性交代完
func _build_term_transition_text(info: Dictionary, next_opponent_id: String) -> String:
	var lines: Array[String] = []
	var winner := String(info.get("winner_id", ""))
	if winner == "player":
		lines.append("🎉 [b]你当选了！[/b]从明天起，你是这片森林新的镇长。")
	else:
		var tie_note := "（票数相同，按规则你败）" if bool(info.get("tie_break", false)) else ""
		lines.append("🏛 %s 当选了本届镇长。%s" % [_npc_display_name(winner), tie_note])

	var rot := _pending_rotation
	var leaver_name := String(rot.get("leaver_name", ""))
	if leaver_name == "":
		leaver_name = _npc_display_name(String(rot.get("leaver", "")))
	var newcomer_name := String(rot.get("nameplate", rot.get("newcomer", "")))
	if leaver_name != "":
		lines.append("🚶 %s 离开了小镇。" % leaver_name)
	var farewell := String(rot.get("farewell", ""))
	if farewell != "":
		lines.append("[color=#c9b98a]%s[/color]" % farewell)
	if newcomer_name != "":
		lines.append("🏠 %s 搬了进来。" % newcomer_name)

	var closed := String(rot.get("closed_facility", ""))
	if closed != "":
		lines.append("（%s 暂时停业了）" % _facility_display_name(closed))
	var reopened := String(rot.get("reopened_facility", ""))
	if reopened != "":
		lines.append("（%s 重新开张了）" % _facility_display_name(reopened))

	var next_name := _npc_display_name(next_opponent_id)
	if next_name != "":
		lines.append("[color=#999999]下一任挑战者：%s[/color]" % next_name)

	return "[center]" + "\n".join(lines) + "[/center]"


func _npc_display_name(id: String) -> String:
	if id == "":
		return ""
	if id == "player":
		return "你"
	var n := _find_npc_node(id)
	if n != null and "animal_name" in n:
		return String(n.animal_name)
	return id


func _facility_display_name(building_id: String) -> String:
	for b in get_tree().get_nodes_in_group("building"):
		if b.get("building_id") == building_id:
			return String(b.display_name).replace("（停业）", "")
	return building_id


func _on_chat_send(animal_id: String, user_text: String) -> void:
	if _current_animal == null or _current_animal.animal_id != animal_id:
		return
	dialog_ui.append_player_line(user_text)

	if not AgentClient.is_connected_to_server():
		dialog_ui.show_npc_line("……（连不上服务器）")
		return

	dialog_ui.set_status("正在思考...")
	AgentClient.request_chat(animal_id, user_text, _build_context(_current_animal))


func _on_gift_send(animal_id: String, item_id: String) -> void:
	if _current_animal == null or _current_animal.animal_id != animal_id:
		return
	if not PlayerInventory.has_item(item_id):
		dialog_ui.set_status("（你没有这个物品）")
		return
	if not AgentClient.is_connected_to_server():
		dialog_ui.show_npc_line("……（连不上服务器，礼物没送出去）")
		return
	# 客户端先扣库存（即使服务端失败也无伤大雅，物品散落即可补给）
	PlayerInventory.remove_item(item_id, 1)
	AgentClient.request_gift(animal_id, item_id, _build_context(_current_animal))


func _on_dialog_finished(_animal_id: String) -> void:
	# NPC 解除 busy，恢复日程
	if _current_animal and is_instance_valid(_current_animal):
		_current_animal.clear_busy()
	_current_animal = null
	# 解锁玩家输入
	player.input_enabled = true
	# 若刚安排了镇务任务给这个 NPC → 对话已关、busy 已清 → 现在开演
	if _pending_mayor.has(_animal_id):
		call_deferred("_start_mayor_perform", _animal_id)


# ---------- 后端回复 ----------

func _on_reply_received(animal_id: String, text: String) -> void:
	if not dialog_ui.is_open():
		return
	if _current_animal == null or _current_animal.animal_id != animal_id:
		return
	dialog_ui.show_npc_line(text)


## 观察线索：NPC 在外面活动时露出的癖好，玩家看见了就能记住（用于日后答题）
func _on_observation_clue(animal_id: String, text: String) -> void:
	var npc := _find_animal(animal_id)
	if npc == null or not is_instance_valid(npc):
		return
	npc.show_emote("👀", 2.0, 0.0)
	npc.show_speech_bubble("（%s）" % text, 4.5)


func _on_error_received(message: String) -> void:
	if not dialog_ui.is_open():
		return
	dialog_ui.set_status("出错：%s" % message)
	dialog_ui.set_input_enabled(true)


func _on_npc_gift(animal_id: String, item_id: String, _item_name: String, message: String) -> void:
	## NPC 升到 love 时赠送签名礼物，加入背包并在对话框显示
	PlayerInventory.add_item(item_id, 1)
	if dialog_ui.is_open():
		dialog_ui.show_npc_gift_note(message)


func _on_canvass_pitch(_animal_id: String, wish: String, bonus: float) -> void:
	## 拉票命中：聊天里点到了 NPC 的心愿，竞选分加成一次（每人每届仅此一次）
	if dialog_ui.is_open():
		dialog_ui.show_npc_gift_note(
			"🗳 [b]拉票成功！[/b]\nTa 记住了你的承诺：%s\n竞选分 +%d" % [wish, int(bonus)]
		)


func _on_quest_offer(_aid: String, qid: String, title: String, desc: String, kind: String, give_item: String, give_count: int, target_npc: String, message_summary: String, collect_item: String, required: int) -> void:
	# 服务端派发任务：deliver 类附带物品
	if give_item != "" and give_count > 0:
		PlayerInventory.add_item(give_item, give_count)
	# 在对话框显示醒目接单
	if dialog_ui.is_open():
		var lines: Array[String] = []
		lines.append("📜 [b]新任务：%s[/b]" % title)
		lines.append(desc)
		match kind:
			"collect":
				if collect_item != "" and required > 0:
					var nm := ItemDB.get_item_name(collect_item)
					lines.append("📦 收集 [b]%s × %d[/b]，凑齐后用 🎁 送给我或拿在背包里跟我聊" % [nm, required])
			"deliver":
				if give_item != "":
					var nm := ItemDB.get_item_name(give_item)
					lines.append("📦 你收到：%s × %d" % [nm, give_count])
				if target_npc != "":
					lines.append("👉 把它送给 [b]%s[/b]" % _npc_label(target_npc))
			"relay":
				if message_summary != "":
					lines.append("💬 要传的话：[i]%s[/i]" % message_summary)
				if target_npc != "":
					lines.append("👉 找 [b]%s[/b] 说出这句话的大致意思" % _npc_label(target_npc))
		dialog_ui.show_npc_gift_note("\n".join(lines))
	# HUD 更新
	if quest_hud and quest_hud.has_method("set_quest"):
		quest_hud.set_quest(qid, title, desc, kind, target_npc, message_summary, collect_item, required, 0)


func _on_quest_progress(_aid: String, qid: String, title: String, desc: String, progress: int, required: int) -> void:
	# NPC 回应时附带的进度提示
	if quest_hud and quest_hud.has_method("set_quest"):
		quest_hud.set_quest(qid, title, desc, "collect", "", "", "", required, progress)
	if dialog_ui.is_open() and required > 0:
		dialog_ui.show_npc_gift_note("📜 [b]%s[/b]\n进度：%d / %d" % [title, progress, required])


func _on_quest_completed(animal_id: String, _qid: String, title: String, _kind: String, reward_item: String, reward_count: int, consume_item: String, consume_count: int) -> void:
	# 先扣再加，避免顺序导致背包异常
	if consume_item != "" and consume_count > 0:
		PlayerInventory.remove_item(consume_item, consume_count)
	if reward_item != "" and reward_count > 0:
		PlayerInventory.add_item(reward_item, reward_count)
	if dialog_ui.is_open():
		var lines: Array[String] = []
		lines.append("✅ [b]任务完成：%s[/b]" % title)
		if consume_item != "":
			var nm := ItemDB.get_item_name(consume_item)
			lines.append("交付：%s × %d" % [nm, consume_count])
		if reward_item != "":
			var rnm := ItemDB.get_item_name(reward_item)
			lines.append("奖励：%s × %d" % [rnm, reward_count])
		dialog_ui.show_npc_gift_note("\n".join(lines))
	# HUD 清空
	if quest_hud and quest_hud.has_method("clear_quest"):
		quest_hud.clear_quest()
	# 让对应 NPC 头顶冒星星
	for n in get_tree().get_nodes_in_group("npc"):
		if n is Animal and n.animal_id == animal_id and n.has_method("show_emote"):
			n.show_emote("🎉", 2.0, 0.0)
			break


func _npc_label(animal_id: String) -> String:
	for n in get_tree().get_nodes_in_group("npc"):
		if n is Animal and n.animal_id == animal_id:
			return n.animal_name
	return animal_id


func _on_affection_changed(animal_id: String, value: int, level: String, delta: int) -> void:
	# 找到对应 animal 节点把好感度状态推过去（emote + 飘字）。
	# 不强制依赖 _current_animal，遍历群组兼容多种触发场景（如未来世界事件）。
	for n in get_tree().get_nodes_in_group("npc"):
		if n is Animal and n.animal_id == animal_id:
			n.update_affection(value, level, delta)
			break


func _on_mood_changed(animal_id: String, emote: String, level: String) -> void:
	for n in get_tree().get_nodes_in_group("npc"):
		if n is Animal and n.animal_id == animal_id:
			n.set_mood(emote, level)
			break


func _on_npc_intent(initiator_id: String, target_id: String, _intent_text: String) -> void:
	## 后端推送 NPC 自发意图：initiator 主动走向 target，到达后发起对话
	if not AgentClient.is_connected_to_server():
		return
	if target_id == "":
		return
	var initiator: Animal = _find_animal(initiator_id)
	var target: Animal = _find_animal(target_id)
	if initiator == null or target == null:
		return
	if initiator.is_busy() or target.is_busy():
		return
	# target 原地等待，面向 initiator
	target.face_to(initiator.global_position)
	# initiator 主动走过去，到达后触发对话
	initiator.approach_for_intent(
		target.global_position,
		func():
			# 到达后双方面对面，置 busy，发 npc_chat
			initiator.face_to(target.global_position)
			target.face_to(initiator.global_position)
			initiator.set_busy(Animal.BusyState.TALKING_NPC, 14.0)
			target.set_busy(Animal.BusyState.TALKING_NPC, 14.0)
			AgentClient.request_npc_chat(
				initiator_id, target_id, initiator.get_current_context()
			)
	)


func _find_animal(animal_id: String) -> Animal:
	for n in get_tree().get_nodes_in_group("npc"):
		if n is Animal and n.animal_id == animal_id:
			return n
	return null


# ---------- 镇务任务表演 ----------

func _on_mayor_task_result(info: Dictionary) -> void:
	## 后端结算完成 → 被指派 NPC 寻路到现场表演（接受时才有）。
	if not info.get("ok", false) or not info.get("accepted", false):
		return
	var exec_id := String(info.get("executor_id", ""))
	if exec_id == "":
		return
	_pending_mayor[exec_id] = info
	# 若该 NPC 不在对话中（对话已关/换了人）→ 立即开演；否则等对话关闭再开
	if _current_animal == null or not is_instance_valid(_current_animal) \
			or _current_animal.animal_id != exec_id:
		_start_mayor_perform(exec_id)


func _start_mayor_perform(exec_id: String) -> void:
	if not _pending_mayor.has(exec_id):
		return
	var info: Dictionary = _pending_mayor[exec_id]
	_pending_mayor.erase(exec_id)
	var actor: Animal = _find_animal(exec_id)
	if actor == null:
		return
	_performing = true
	actor.clear_busy()   # 确保脱离 TALKING_PLAYER，intent 才能驱动移动
	_perform_mayor_task(actor, info)


# ---------- 镇务情境（任务刷新即在世界里布置脏物/病人等）----------

func _on_mayor_task_state(info: Dictionary) -> void:
	if info.get("active", false):
		_setup_situation(info.get("task", {}))
	elif not _performing:
		# 任务清空且非表演中 → 拆除残留情境
		_teardown_situation()


## 任务刷新时布置情境（幂等：同一 task_id 不重复布置）
func _setup_situation(task: Dictionary) -> void:
	if task.is_empty():
		return
	var tid := int(task.get("id", 0))
	if int(_situation.get("task_id", -1)) == tid:
		return
	_teardown_situation()
	var ttype := String(task.get("task_type", ""))
	var target_id := String(task.get("target_id", ""))
	var markers: Array = []
	var spots: Array[Vector2] = []
	match ttype:
		"clean":
			var n := 3 + (randi() % 2)
			for i in n:
				var p := _random_road_point()
				spots.append(p)
				markers.append(_spawn_marker(p, "🤢"))
		"repair_sewer":
			var p := _random_road_point()
			spots.append(p)
			markers.append(_spawn_marker(p, "🔧"))
		"cure_epidemic":
			var patient := _find_animal(target_id)
			if is_instance_valid(patient):
				patient.set_status_effect("🤒", Color(0.7, 1.0, 0.7))
				patient.set_busy(Animal.BusyState.TALKING_NPC, 0.0)  # 站定示病，便于寻找
		"subdue_drunk":
			var drunk := _find_animal(target_id)
			if is_instance_valid(drunk):
				drunk.set_status_effect("🍺", Color(1.0, 0.7, 0.7))
				drunk.set_busy(Animal.BusyState.TALKING_NPC, 0.0)
	_situation = {
		"task_id": tid, "type": ttype, "target_id": target_id,
		"markers": markers, "spots": spots,
	}


func _teardown_situation() -> void:
	for m in _situation.get("markers", []):
		_clear_marker(m)
	var tgt := String(_situation.get("target_id", ""))
	if tgt != "":
		var a := _find_animal(tgt)
		if is_instance_valid(a):
			a.clear_status_effect()
			a.clear_busy()
	_situation = {}


func _random_road_point() -> Vector2:
	# 路网节点必在道路/陆地上、可达；避开玩家脚下太近处
	var p: Vector2 = PathNetwork.random_point(player.global_position, 60.0)
	if p == Vector2.ZERO:
		return player.global_position
	return p


## 世界特效标记（脏物/维修点等），返回节点，用 _clear_marker 移除
func _spawn_marker(pos: Vector2, emoji: String) -> Node2D:
	var holder := Node2D.new()
	holder.z_index = 500
	var lbl := Label.new()
	lbl.text = emoji
	lbl.add_theme_font_size_override("font_size", 28)
	lbl.position = Vector2(-14, -20)
	holder.add_child(lbl)
	add_child(holder)
	holder.global_position = pos
	return holder


func _clear_marker(node) -> void:
	if is_instance_valid(node):
		node.queue_free()


## 让 actor 以自发意图走到 pos 并等待到达（到达/超时都会返回）
func _walk_and_wait(actor: Animal, pos: Vector2) -> void:
	if not is_instance_valid(actor):
		return
	var done := [false]
	actor.show_emote("🏃", 1.2, 0.0)
	actor.approach_for_intent(pos, func(): done[0] = true)
	var guard := 0.0
	while not done[0] and is_instance_valid(actor):
		await get_tree().process_frame
		guard += get_process_delta_time()
		if guard > 20.0:
			break


func _perform_mayor_task(actor: Animal, info: Dictionary) -> void:
	var task_type := String(info.get("task_type", ""))
	var target_id := String(info.get("target_id", ""))
	var outcome := String(info.get("outcome", "ok"))
	var injured := bool(info.get("injured", false))
	var result_line := String(info.get("result_line", ""))
	if quest_hud and quest_hud.has_method("set_mayor_in_progress"):
		quest_hud.set_mayor_in_progress(actor.animal_name)

	match task_type:
		"subdue_drunk":
			await _perform_subdue(actor, target_id, injured)
		"cure_epidemic":
			await _perform_cure(actor, target_id)
		"archive":
			await _perform_archive(actor)
		"repair_sewer":
			await _perform_repair(actor)
		"clean":
			await _perform_clean(actor)
		_:
			await _walk_and_wait(actor, _random_road_point())

	# 表演结束 → 清理情境
	_teardown_situation()
	_performing = false
	if not is_instance_valid(actor):
		return
	# ── 结果反馈：表情 + 台词 + HUD + 中央横幅 ──
	if injured:
		actor.modulate = Color(1.0, 0.6, 0.6)
		actor.show_emote("💫", 2.5, 0.0)
	elif outcome == "great":
		actor.show_emote("✨", 2.5, 0.0)
	elif outcome == "botch":
		actor.show_emote("💢", 2.5, 0.0)
	else:
		actor.show_emote("👌", 2.0, 0.0)
	if result_line != "":
		actor.show_speech_bubble(result_line, 4.0)
	var sb := int(info.get("score_before", -1))
	var sa := int(info.get("score_after", -1))
	if quest_hud and quest_hud.has_method("set_mayor_result"):
		quest_hud.set_mayor_result(actor.animal_name, outcome, injured, sb, sa)
	_flash_mayor_banner(actor.animal_name, outcome, injured, sb, sa)
	await get_tree().create_timer(3.0).timeout
	if is_instance_valid(actor):
		actor.modulate = Color(1, 1, 1)
		actor.clear_busy()


## 中央横幅提示镇务结果 + 声望变化（复用 ElectionHUD 横幅）
func _flash_mayor_banner(exec_name: String, outcome: String, injured: bool, sb: int, sa: int) -> void:
	var hud := get_node_or_null("ElectionHUD")
	if hud == null or not hud.has_method("flash_mayor_toast"):
		return
	var title := "🏛 镇务圆满解决！"
	if outcome == "botch":
		title = "🏛 镇务闹出麻烦…"
	elif outcome == "ok":
		title = "🏛 镇务勉强了事"
	var sub := ""
	if sb >= 0 and sa >= 0:
		var d := sa - sb
		if d > 0:
			sub = "镇长声望 %d → %d（[color=#8de89a]+%d[/color]）" % [sb, sa, d]
		elif d < 0:
			sub = "镇长声望 %d → %d（[color=#ff8a8a]%d[/color]）" % [sb, sa, d]
		else:
			sub = "镇长声望暂无明显变化（%d）" % sa
	if injured:
		sub += "  ·  %s受了伤" % exec_name
	hud.flash_mayor_toast(title, sub)


# ── 制服酒鬼：走到（已在情境中醉倒的）酒鬼身边打斗 ──
func _perform_subdue(actor: Animal, drunk_id: String, injured: bool) -> void:
	var drunk: Animal = _find_animal(drunk_id)
	var pos: Vector2 = drunk.global_position if is_instance_valid(drunk) else _random_road_point()
	await _walk_and_wait(actor, pos)
	if not is_instance_valid(actor):
		return
	actor.set_busy(Animal.BusyState.TALKING_NPC, 20.0)
	if is_instance_valid(drunk):
		drunk.clear_status_effect()   # 解除持久醉态，才能播打斗 emote
		drunk.set_busy(Animal.BusyState.TALKING_NPC, 20.0)
		drunk.modulate = Color(1.0, 0.7, 0.7)
		drunk.show_speech_bubble("嗝…谁…谁要管我！", 2.0)
		actor.face_to(drunk.global_position)
	actor.show_speech_bubble("镇长有令，跟我回去醒醒酒！", 2.0)
	for i in 3:
		if not is_instance_valid(actor):
			return
		actor.show_emote("⚔️", 0.8, 0.0)
		if is_instance_valid(drunk):
			drunk.show_emote("💥", 0.8, 0.0)
		await get_tree().create_timer(0.7).timeout
	if not injured and is_instance_valid(drunk):
		drunk.show_emote("😵", 1.5, 0.0)
		drunk.show_speech_bubble("好…好吧，我不闹了…", 2.5)
	if is_instance_valid(drunk):
		drunk.modulate = Color(1, 1, 1)
		drunk.clear_busy()


# ── 治疗传染病：走到（已在情境中患病的）病人身边治疗 ──
func _perform_cure(actor: Animal, patient_id: String) -> void:
	var patient: Animal = _find_animal(patient_id)
	var pos: Vector2 = patient.global_position if is_instance_valid(patient) else _random_road_point()
	await _walk_and_wait(actor, pos)
	if not is_instance_valid(actor):
		return
	actor.set_busy(Animal.BusyState.TALKING_NPC, 20.0)
	if is_instance_valid(patient):
		actor.face_to(patient.global_position)
	await _repeat_emote(actor, "💊", 3)
	if is_instance_valid(patient):
		patient.clear_status_effect()   # 病愈：解除持久病态
		patient.show_emote("😌", 2.0, 0.0)
		patient.show_speech_bubble("咦…好多了，谢谢镇长！", 2.5)
		patient.clear_busy()


# ── 整理档案：进镇长家(home_new)隐身整理再出来 ──
func _perform_archive(actor: Animal) -> void:
	var pos := LocationDB.get_pos("home_new")
	if pos == Vector2.ZERO:
		pos = player.global_position
	await _walk_and_wait(actor, pos)
	if not is_instance_valid(actor):
		return
	actor.set_busy(Animal.BusyState.TALKING_NPC, 20.0)
	actor.show_emote("📚", 1.2, 0.0)
	actor.show_speech_bubble("进镇长屋整理档案去。", 1.8)
	await get_tree().create_timer(1.0).timeout
	if not is_instance_valid(actor):
		return
	actor.visible = false   # 进屋
	await get_tree().create_timer(2.5).timeout
	if is_instance_valid(actor):
		actor.visible = true    # 出屋


# ── 修下水道：走到情境已布置的维修点修理 ──
func _perform_repair(actor: Animal) -> void:
	var spots: Array = _situation.get("spots", [])
	var pos: Vector2 = spots[0] if spots.size() > 0 else _random_road_point()
	await _walk_and_wait(actor, pos)
	if not is_instance_valid(actor):
		return
	actor.set_busy(Animal.BusyState.TALKING_NPC, 20.0)
	actor.face_to(pos)
	await _repeat_emote(actor, "🔧", 3)
	for m in _situation.get("markers", []):
		_clear_marker(m)
	_situation["markers"] = []


# ── 打扫：逐个走到情境已布置的脏物点清扫 ──
func _perform_clean(actor: Animal) -> void:
	var spots: Array = _situation.get("spots", [])
	var markers: Array = _situation.get("markers", [])
	if spots.is_empty():
		# 兜底：无情境（异常）则临时生成
		for i in 3:
			var p := _random_road_point()
			spots.append(p)
			markers.append(_spawn_marker(p, "🤢"))
	for i in spots.size():
		if not is_instance_valid(actor):
			break
		actor.clear_busy()   # 解除忙碌以便 intent 走向下一处
		await _walk_and_wait(actor, spots[i])
		if not is_instance_valid(actor):
			break
		actor.set_busy(Animal.BusyState.TALKING_NPC, 8.0)
		actor.face_to(spots[i])
		actor.show_emote("🧹", 1.0, 0.0)
		await get_tree().create_timer(0.9).timeout
		if i < markers.size():
			_clear_marker(markers[i])


func _repeat_emote(actor: Animal, icon: String, times: int) -> void:
	for i in times:
		if not is_instance_valid(actor):
			return
		actor.show_emote(icon, 0.9, 0.0)
		await get_tree().create_timer(0.8).timeout


# ---------- 选举信号 ----------

func _on_opponent_action(info: Dictionary) -> void:
	## 对手 NPC 拜访某 voter NPC，对手头顶弹气泡显示拉票台词。
	var opponent_id := String(info.get("candidate_id", ""))
	var text := String(info.get("text", ""))
	if opponent_id == "" or text == "":
		return
	var opponent: Animal = _find_animal(opponent_id)
	if opponent and opponent.has_method("show_speech_bubble"):
		opponent.show_speech_bubble(text, 5.0)

	# 挖墙脚会真的扣好感。不给反馈的话，玩家只会看到好感莫名下跌，
	# 却不知道是被人撬了——那这个机制对玩家就是不可见的，等于没做。
	if String(info.get("action_type", "")) != "poach":
		return
	var effect = info.get("mechanical_effect", {})
	var delta := 0
	if typeof(effect) == TYPE_DICTIONARY:
		delta = int(effect.get("affection_delta", 0))
	if delta >= 0:
		return
	var target: Animal = _find_animal(String(info.get("target_npc", "")))
	if target and target.has_method("show_speech_bubble"):
		target.show_speech_bubble("……（对你的好感 %d）" % delta, 4.0)


func _on_election_result(info: Dictionary) -> void:
	## D7 投票结果到来 → 通过 ElectionHUD 触发投票演出。
	var hud := get_node_or_null("ElectionHUD")
	if hud and hud.has_method("show_vote_result"):
		hud.show_vote_result(info)


func _input(event: InputEvent) -> void:
	## 调试快捷键：Ctrl+V 立即触发当前任期投票（测试演出用）。
	var f := get_viewport().gui_get_focus_owner()
	if f is LineEdit or f is TextEdit:
		return
	if event is InputEventKey and event.pressed and event.keycode == KEY_V and event.ctrl_pressed:
		print("[main] DEBUG: 强制投票结算")
		AgentClient.request_debug_force_vote()
	## 调试快捷键：Ctrl+O 立即触发一批对手行动（验证追赶用）。
	if event is InputEventKey and event.pressed and event.keycode == KEY_O and event.ctrl_pressed:
		print("[main] DEBUG: 触发对手行动")
		AgentClient.request_debug_opponent_action()
	## 调试快捷键：Ctrl+M 立即刷新一个镇务任务（需玩家为现任镇长）。
	if event is InputEventKey and event.pressed and event.keycode == KEY_M and event.ctrl_pressed:
		print("[main] DEBUG: 刷新镇务任务")
		if AgentClient.has_method("request_debug_spawn_mayor_task"):
			AgentClient.request_debug_spawn_mayor_task()
	## GM 快捷键：Ctrl+G 让玩家直接成为现任镇长。
	if event is InputEventKey and event.pressed and event.keycode == KEY_G and event.ctrl_pressed:
		print("[main] GM: 直升镇长")
		if AgentClient.has_method("request_debug_make_mayor"):
			AgentClient.request_debug_make_mayor()


# ---------- 上下文构造 ----------

## 任务追踪：玩家访问过的地点 + 聊过的 NPC（最多保留最近 8 项）
var _visited_locations: Array[String] = []
var _talked_to_npcs:    Array[String] = []


func _build_context(animal: Animal) -> Dictionary:
	var loc_id: String = animal.get_target_location()
	# 附近其他 NPC 名字（用于"附近还有谁"prompt 注入）
	var nearby: Array = []
	for n in get_tree().get_nodes_in_group("npc"):
		if n == animal: continue
		if not is_instance_valid(n): continue
		if n.global_position.distance_to(animal.global_position) > 120.0: continue
		var nm: String = n.animal_name if "animal_name" in n else ""
		if nm != "": nearby.append(nm)
	# 玩家背包（任务系统用）
	var inv: Dictionary = PlayerInventory.get_all() if has_node("/root/PlayerInventory") else {}
	return {
		"time": WorldClock.format_time(),
		"game_day": WorldClock.get_day(),
		"location": loc_id,
		"location_label": LocationDB.get_label(loc_id),
		"intent": animal.get_current_intent(),
		"nearby_npcs": nearby,
		"inventory": inv,
		"visited_locations": _visited_locations.duplicate(),
		"talked_to_npcs": _talked_to_npcs.duplicate(),
	}


## 玩家访问/聊天历史追踪（任务完成判定用）
func _track_visit(loc_id: String) -> void:
	if loc_id == "" or loc_id in _visited_locations:
		return
	_visited_locations.append(loc_id)
	if _visited_locations.size() > 12:
		_visited_locations.pop_front()


func _track_talked_to(animal_id: String) -> void:
	if animal_id == "":
		return
	# 移到最后（最近）
	_talked_to_npcs.erase(animal_id)
	_talked_to_npcs.append(animal_id)
	if _talked_to_npcs.size() > 12:
		_talked_to_npcs.pop_front()


# ---------- NPC 在场名单（动态实例化）----------
#
# 镇上有谁由后端的 npc_roster 表说了算，而不是 main.tscn 里写死几个节点。
# 换届会有人离镇、有人搬入，写死的节点没法反映这件事——玩家看不见轮换，
# 这套设计就等于没做。
const ANIMAL_SCENE := preload("res://scenes/entities/animal.tscn")

# 后端连不上时的兜底名单：与 roster.py 的 DEFAULT_PRESENT 一致。
# 没有它，单机断网启动会是一座空镇。
const FALLBACK_ROSTER := [
	{"animal_id": "bear_baker",    "spawn": Vector2(200, 240)},
	{"animal_id": "fox_postman",   "spawn": Vector2(992, 240)},
	{"animal_id": "herbalist_cui", "spawn": Vector2(-103, 229)},
	{"animal_id": "traveler_lan",  "spawn": Vector2(728, 714)},
	{"animal_id": "pirate_lao",    "spawn": Vector2(700, 240)},
	{"animal_id": "mystic_xuan",   "spawn": Vector2(1145, 425)},
]

var _roster_built := false


func _spawn_npcs_fallback() -> void:
	if _roster_built:
		return
	for e in FALLBACK_ROSTER:
		_spawn_npc(String(e["animal_id"]), e["spawn"], {})
	_roster_built = true
	print("[Main] 后端未响应，按兜底名单生成 %d 个 NPC" % FALLBACK_ROSTER.size())


func _on_roster_received(present: Array) -> void:
	if _roster_built:
		return
	if present.is_empty():
		_spawn_npcs_fallback()
		return
	for e in present:
		if typeof(e) != TYPE_DICTIONARY:
			continue
		_spawn_npc(
			String(e.get("animal_id", "")),
			_to_vec(e.get("spawn", [])),
			e.get("schedule_override", {}),
		)
	_roster_built = true
	print("[Main] 按后端名单生成 %d 个 NPC" % present.size())


func _to_vec(arr) -> Vector2:
	if arr is Array and arr.size() >= 2:
		return Vector2(float(arr[0]), float(arr[1]))
	return Vector2(600, 350)


func _spawn_npc(animal_id: String, pos: Vector2, sched_override) -> Node:
	if animal_id == "":
		return null
	if _find_npc_node(animal_id) != null:
		return null
	var path := "res://data/animals/%s.json" % animal_id
	if not FileAccess.file_exists(path):
		push_error("Main: 找不到 persona %s" % path)
		return null
	var npc := ANIMAL_SCENE.instantiate()
	npc.persona_file = path
	npc.position = pos
	# 必须在 add_child 之前赋值：_ready() 里就会读它来覆盖日程
	if typeof(sched_override) == TYPE_DICTIONARY and not sched_override.is_empty():
		npc.schedule_override = sched_override
	add_child(npc)
	return npc


func _find_npc_node(animal_id: String) -> Node:
	for n in get_tree().get_nodes_in_group("npc"):
		if n.animal_id == animal_id:
			return n
	return null


func _on_npc_rotation(info: Dictionary) -> void:
	## 先缓存，不立即落地——换届黑幕过渡（_do_term_transition）播完叙事文案后才真正换人，
	## 否则玩家会在毫无征兆的情况下看到 NPC 瞬间消失/出现。
	if String(info.get("newcomer", "")) == "":
		return
	_pending_rotation = info


func _apply_rotation(info: Dictionary) -> void:
	var leaver := String(info.get("leaver", ""))
	var newcomer := String(info.get("newcomer", ""))
	if newcomer == "":
		return

	# 离镇者退场。若正在跟他对话，先关掉对话框，否则会对着空节点。
	var old := _find_npc_node(leaver)
	if old != null:
		if _current_animal == old:
			dialog_ui.close()
			_current_animal = null
		old.queue_free()

	_spawn_npc(newcomer, _to_vec(info.get("spawn", [])),
			info.get("schedule_override", {}))

	# 门牌换名：building_id 不动（path_network/日程/spawners 三处都引用它），
	# 只改展示名，这正是 building.display_name 带 setter 的意义。
	_rename_building(String(info.get("home_id", "")),
			String(info.get("nameplate", "")))

	var closed := String(info.get("closed_facility", ""))
	if closed != "":
		_set_facility_closed(closed, true)
	var reopened := String(info.get("reopened_facility", ""))
	if reopened != "":
		_set_facility_closed(reopened, false)

	print("[Main] 轮换：%s 离镇 → %s 迁入 %s" % [leaver, newcomer, info.get("home_id", "")])


func _rename_building(building_id: String, new_name: String) -> void:
	if building_id == "" or new_name == "":
		return
	for b in get_tree().get_nodes_in_group("building"):
		if b.get("building_id") == building_id:
			b.display_name = "%s的家" % new_name
			return


func _set_facility_closed(building_id: String, closed: bool) -> void:
	for b in get_tree().get_nodes_in_group("building"):
		if b.get("building_id") != building_id:
			continue
		var base := String(b.display_name).replace("（停业）", "")
		b.display_name = base + ("（停业）" if closed else "")
		return
