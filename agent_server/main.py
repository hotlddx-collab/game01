"""FastAPI + WebSocket 主服务。

启动: python main.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import uvicorn

from llm import LLMClient
from personas import load_all_personas
from agent import AgentManager
from db import init_schema
from memory import MemoryStore
from profile import PlayerProfile
from world_events import WorldEventStore
from affection import AffectionStore
from gifts import GiftStore
from reflection import ReflectionStore, IntentStore
from election import ElectionStore
from opponent_ai import OpponentAI
from promises import PromiseStore
from debate import DebateManager
from power import PowerManager, ACTIONS as POWER_ACTIONS


# ---------- 启动初始化 ----------
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("agent_server")

# 初始化 SQLite schema
init_schema()
log.info("数据库就绪 (town.db)")

app = FastAPI(title="怪物森林 Agent Server")

llm: LLMClient = LLMClient()
personas = load_all_personas()
memory_store = MemoryStore()
profile_store = PlayerProfile()
world_store = WorldEventStore()
affection_store = AffectionStore()
gift_store = GiftStore()
reflection_store = ReflectionStore()
intent_store = IntentStore()
from milestones import MilestoneStore
from db import DB_PATH
milestone_store = MilestoneStore(DB_PATH)
from quests import QuestStore, QuestEngine
quest_store = QuestStore(DB_PATH)
quest_engine = QuestEngine(quest_store)
manager = AgentManager(
    personas, llm, memory_store, profile_store, world_store,
    affection_store, gift_store, reflection_store,
    milestone_store=milestone_store,
    quest_engine=quest_engine,
)
log.info("加载 personas: %s", manager.all_ids())

# 镇长选举系统（D1 骨架）
promise_store = PromiseStore()
election_store = ElectionStore(
    npc_ids=list(manager.all_ids()),
    affection_store=affection_store,
    world_store=world_store,
    promise_store=promise_store,
)
log.info("[election] ElectionStore 就绪 npc=%d", len(manager.all_ids()))


# 当期对手不派任务（对手非投票人，给它完成的承诺无法加选票）
def _is_current_opponent(npc_id: str) -> bool:
    try:
        term = election_store.get_active_term()
        return bool(term) and term.get("opponent_id") == npc_id
    except Exception:
        return False


quest_engine.is_opponent = _is_current_opponent

# ---- quest ↔ promise 钩子 ----
# 每次 quest accept/complete 时，同步建 / 兑现 promise
_orig_mark_active = quest_store.mark_active
_orig_mark_completed = quest_store.mark_completed


def _quest_mark_active_with_promise(qid: str) -> None:
    _orig_mark_active(qid)
    try:
        q = quest_engine.defs.get(qid, {})
        npc_id = q.get("npc_id", "")
        term = election_store.get_active_term()
        if term and npc_id:
            day = _last_known_game_day if _last_known_game_day >= 0 else int(term.get("start_day", 0))
            # deadline = 任期结束日（start_day + 6 = D7 结算日）
            deadline = int(term["start_day"]) + 6
            promise_store.create(
                term_id=int(term["term_id"]),
                candidate_id="player",
                npc_id=npc_id,
                quest_id=qid,
                accept_day=day,
                deadline_day=deadline,
            )
            log.info("[promise] 建立 quest=%s npc=%s term=%d day=%d→%d",
                     qid, npc_id, int(term["term_id"]), day, deadline)
    except Exception as e:
        log.warning("[promise] 建立失败 qid=%s: %s", qid, e)


def _quest_mark_completed_with_promise(qid: str) -> None:
    _orig_mark_completed(qid)
    try:
        day = _last_known_game_day if _last_known_game_day >= 0 else 0
        promise_store.fulfill_by_quest(qid, day)
    except Exception as e:
        log.warning("[promise] 兑现失败 qid=%s: %s", qid, e)


quest_store.mark_active = _quest_mark_active_with_promise
quest_store.mark_completed = _quest_mark_completed_with_promise

# 对手 AI（D3）
opponent_ai = OpponentAI(
    election_store=election_store,
    personas=personas,
    llm=llm,
    world_store=world_store,
    memory_store=memory_store,
)
log.info("[opponent_ai] OpponentAI 就绪")

# 辩论日系统（D7）
debate_manager = DebateManager(
    election_store=election_store,
    personas=personas,
    llm=llm,
    affection_store=affection_store,
)
log.info("[debate] DebateManager 就绪")

# 任期权力点系统（D9）
power_manager = PowerManager(
    election_store=election_store,
    affection_store=affection_store,
    world_store=world_store,
    personas=personas,
    llm=llm,
)
log.info("[power] PowerManager 就绪")

# 记录上次触发反思的游戏日，避免同日重复触发
_last_reflect_day: int = -1
_last_election_recompute_day: int = -1
_last_opponent_action_day: int = -1
_last_known_game_day: int = -1   # 任意 time_tick 后更新，供 promise 等模块取当前 day


# ---------- HTTP 健康检查 ----------
@app.get("/")
async def root():
    return {"status": "ok", "animals": manager.all_ids()}


# ---------- WebSocket ----------
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    log.info("client connected")
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await _send_error(ws, "JSON 解析失败")
                continue

            await _handle_message(ws, msg)
    except WebSocketDisconnect:
        log.info("client disconnected")
    except Exception as e:
        log.exception("ws error: %s", e)


async def _handle_message(ws: WebSocket, msg: dict) -> None:
    msg_type = msg.get("type")

    # 游戏时间同步（每游戏日 22:00 触发全员反思，不回包）
    if msg_type == "time_tick":
        await _handle_time_tick(msg, ws)
        return

    # NPC↔NPC 对话（特殊：需要两个 agent，不走 animal_id 单查）
    if msg_type == "npc_chat":
        await _handle_npc_chat(ws, msg)
        return

    # 玩家偷听
    if msg_type == "eavesdrop":
        await _handle_eavesdrop(ws, msg)
        return

    # 选举状态查询（C→S 拉取，回 election_state）
    if msg_type == "election_query":
        await _handle_election_query(ws, msg)
        return

    # 调试：强制触发当前任期投票结算（玩家测试演出用）
    if msg_type == "debug_force_vote":
        await _handle_debug_force_vote(ws, msg)
        return

    # 承诺池查询（玩家按 P 键 / 进游戏时拉取）
    if msg_type == "promise_query":
        await _handle_promise_query(ws, msg)
        return

    # 辩论日：开场拉题 / 对手反驳 / 提交评分
    if msg_type == "debate_start":
        await _handle_debate_start(ws, msg)
        return
    if msg_type == "debate_rebut":
        await _handle_debate_rebut(ws, msg)
        return
    if msg_type == "debate_submit":
        await _handle_debate_submit(ws, msg)
        return

    # 任期权力点（D9）：查询 / 执行行动 / 调试授权
    if msg_type == "power_query":
        await _handle_power_query(ws, msg)
        return
    if msg_type == "power_action":
        await _handle_power_action(ws, msg)
        return
    if msg_type == "debug_grant_power":
        await _handle_debug_grant_power(ws, msg)
        return

    animal_id = msg.get("animal_id", "")
    agent = manager.get(animal_id)
    if agent is None:
        await _send_error(ws, f"未知 animal_id: {animal_id}")
        return

    context = msg.get("context", {})
    # 顺手更新 game_day（promise 钩子用）
    global _last_known_game_day
    try:
        gd = int(context.get("game_day", -1))
        if gd >= 0:
            _last_known_game_day = gd
    except Exception:
        pass

    # 注入当期选举身份，让 NPC 知道自己/对手是否在参选
    if msg_type in ("greet", "chat", "gift"):
        try:
            context["election"] = _build_election_context(animal_id, context)
        except Exception as e:
            log.debug("[election] 注入身份失败: %s", e)

    try:
        if msg_type == "greet":
            result = await agent.greet(context)
        elif msg_type == "chat":
            user_text = msg.get("user_text", "")
            if not user_text.strip():
                await _send_error(ws, "user_text 为空")
                return
            result = await agent.reply(user_text, context)
        elif msg_type == "gift":
            item_id = msg.get("item_id", "")
            if not item_id:
                await _send_error(ws, "gift 缺少 item_id")
                return
            result = await agent.receive_gift(item_id, context)
        elif msg_type == "reset":
            agent.reset_history()
            await ws.send_text(
                json.dumps({"type": "ok", "animal_id": animal_id})
            )
            return
        else:
            await _send_error(ws, f"未知 type: {msg_type}")
            return
    except Exception as e:
        log.exception("LLM 调用失败")
        await _send_error(ws, f"LLM 错误: {e}")
        return

    payload = {
        "type": "reply",
        "animal_id": animal_id,
        "text": result["text"],
        "affection": result.get("affection", {}),
        "ok": True,
    }
    if "gift" in result:
        payload["gift"] = result["gift"]
    if "npc_gift" in result:
        payload["npc_gift"] = result["npc_gift"]
    if "milestone" in result:
        payload["milestone"] = result["milestone"]
    for k in ("quest_offer", "quest_progress", "quest_completed"):
        if k in result:
            payload[k] = result[k]

    # 对话驱动意图：NPC 答应去找某人 → 写 intent_store + 回包通知客户端
    intent_data = result.get("intent")
    if intent_data and intent_data.get("agreed") and intent_data.get("target_id"):
        target_id = intent_data["target_id"]
        known_ids = set(manager.all_ids())
        if target_id in known_ids and target_id != animal_id:
            game_day = int(context.get("game_day", 0))
            activate_hour = 10 + (abs(hash(f"{animal_id}{game_day}")) % 7)
            intent_store.add(
                animal_id,
                intent_data.get("summary", "去找人"),
                game_day + 1,
                target_id=target_id,
                activate_hour=activate_hour,
            )
            target_agent = manager.get(target_id)
            target_name = target_agent.name if target_agent else target_id
            payload["intent"] = {
                "target_id": target_id,
                "target_name": target_name,
                "summary": intent_data.get("summary", ""),
            }
            log.info(
                "[intent] %s 承诺 day=%d h=%d: %s → %s",
                animal_id, game_day + 1, activate_hour,
                intent_data.get("summary", ""), target_id,
            )

    await ws.send_text(json.dumps(payload, ensure_ascii=False))

    # 玩家与 NPC 互动后 affection / event 可能变化 → 顺手推一次选举状态让 HUD 实时刷新
    if msg_type in ("chat", "gift", "greet"):
        try:
            game_day_ctx = int(context.get("game_day", 0))
            await _broadcast_election_state(ws, game_day_ctx)
            await _broadcast_promise_state(ws, game_day_ctx)
        except Exception as e:
            log.debug("[election] 推送跟进状态失败: %s", e)


async def _handle_npc_chat(ws: WebSocket, msg: dict) -> None:
    speaker_id = msg.get("speaker_id", "")
    listener_id = msg.get("listener_id", "")
    context = msg.get("context", {})
    if not speaker_id or not listener_id:
        await _send_error(ws, "npc_chat 需要 speaker_id 和 listener_id")
        return
    if speaker_id == listener_id:
        await _send_error(ws, "npc_chat 不能自言自语")
        return
    if manager.get(speaker_id) is None or manager.get(listener_id) is None:
        await _send_error(ws, "未知 speaker 或 listener")
        return

    turns = int(os.getenv("NPC_CHAT_TURNS", "3"))
    bubble_gap = float(os.getenv("NPC_CHAT_GAP_SEC", "2.5"))

    try:
        first_packet = True
        async for line_pkt in manager.trigger_npc_chat_session(
            speaker_id, listener_id, context, turns=turns
        ):
            if not first_packet:
                # 句间气泡显示节奏
                await asyncio.sleep(bubble_gap)
            first_packet = False
            await ws.send_text(
                json.dumps({"type": "npc_chat_reply", **line_pkt, "ok": True}, ensure_ascii=False)
            )
    except Exception as e:
        log.exception("npc_chat session 失败")
        await _send_error(ws, f"LLM 错误: {e}")
        return


async def _handle_eavesdrop(ws: WebSocket, msg: dict) -> None:
    """玩家偷听到一句 NPC 对话：双方各加记忆 + 写一条世界事件。"""
    speaker_id = msg.get("speaker_id", "")
    listener_id = msg.get("listener_id", "")
    text = msg.get("text", "")
    context = msg.get("context", {})

    speaker = manager.get(speaker_id)
    listener = manager.get(listener_id)
    if speaker is None or listener is None:
        await _send_error(ws, "未知 speaker 或 listener")
        return
    if not text.strip():
        await _send_error(ws, "eavesdrop text 为空")
        return

    game_time = context.get("time", "")
    location = context.get("location", "")
    location_label = context.get("location_label", "")
    short = text[:60]

    # speaker 视角：自己刚说的话被玩家听到
    speaker.memory.add(
        speaker_id,
        f"我对{listener.name}说「{short}」时，被玩家听到了",
        type="event",
        speaker="self",
        importance=4,
        game_time=game_time,
    )
    # listener 视角：和 speaker 的对话被玩家听到
    listener.memory.add(
        listener_id,
        f"{speaker.name}对我说「{short}」时，被玩家听到了",
        type="event",
        speaker="self",
        importance=4,
        game_time=game_time,
    )
    # 世界事件：玩家偷听过这段对话
    world_store.add(
        actor="player",
        description=f"在{location_label or '附近'}偷听到{speaker.name}对{listener.name}说的话",
        location=location,
        game_time=game_time,
    )

    log.info("[eavesdrop] player overheard %s→%s: %s", speaker_id, listener_id, short)
    await ws.send_text(json.dumps({"type": "ok", "context": "eavesdrop"}, ensure_ascii=False))


def _build_election_context(animal_id: str, context: dict) -> dict:
    """构造注入 NPC system prompt 的选举身份信息。

    candidate（对手 NPC）→ 知道自己在参选；voter → 知道自己有投票权。
    """
    game_day = int(context.get("game_day", _last_known_game_day if _last_known_game_day >= 0 else 0))
    term = election_store.ensure_term_active(game_day)
    opponent_id = term["opponent_id"]
    day_index = election_store.day_index_in_term(term, game_day)
    phase = election_store.phase_of(day_index)
    phase_label = {"campaign": "拉票期", "debate": "辩论日", "vote": "投票日"}.get(phase, "拉票期")

    def _name(nid: str) -> str:
        ag = manager.get(nid)
        return ag.name if ag else nid

    is_candidate = (animal_id == opponent_id)
    opp_name = "玩家" if is_candidate else _name(opponent_id)

    return {
        "role": "candidate" if is_candidate else "voter",
        "term_no": int(term["term_id"]),
        "opponent_name": opp_name,
        "day_index": day_index,
        "term_days": 7,
        "phase_label": phase_label,
        "is_incumbent": election_store.is_incumbent(int(term["term_id"]), animal_id),
    }


async def _handle_election_query(ws: WebSocket, msg: dict) -> None:
    """客户端拉取选举状态。

    入参：{"type": "election_query", "game_day": N}
    出参：{"type": "election_state", ...election_view}
    """
    game_day = int(msg.get("game_day", 0))
    view = election_store.get_current_term_view(game_day)
    payload = {"type": "election_state", **view, "ok": True}
    await ws.send_text(json.dumps(payload, ensure_ascii=False))


async def _handle_debug_force_vote(ws: WebSocket, msg: dict) -> None:
    """玩家用：把当前任期推到 D7 直接投票，省去等 7 个游戏日。

    入参：{"type": "debug_force_vote", "game_day": N}
    返回：election_result（如同正常 D7 22:00 自动结算）
    """
    game_day = int(msg.get("game_day", 0))
    term = election_store.ensure_term_active(game_day)
    needed_day = int(term["start_day"]) + 6  # day_index=7
    use_day = max(game_day, needed_day)
    election_store.recompute_and_persist_weights(term, use_day)
    settle = election_store.settle_term_if_due(use_day)
    if settle is not None:
        try:
            await ws.send_text(json.dumps({
                "type": "election_result",
                "ok": True,
                **settle,
            }, ensure_ascii=False))
        except Exception as e:
            log.warning("[election] debug force vote 推送失败: %s", e)
        winner = settle["winner_id"]
        world_store.add(
            actor=winner,
            description=(
                f"[DEBUG] 第 {settle['settled_term_id']} 届选举强制结算，"
                f"{'玩家' if winner == 'player' else winner} 当选"
            ),
        )
        await _broadcast_election_state(ws, use_day + 1)
    else:
        await _send_error(ws, "强制结算失败：可能任期不存在或已结束")


async def _broadcast_election_state(ws: WebSocket, game_day: int) -> None:
    """主动推送选举状态（time_tick 跨日时调）。"""
    try:
        view = election_store.get_current_term_view(game_day)
        await ws.send_text(json.dumps({"type": "election_state", **view, "ok": True}, ensure_ascii=False))
    except Exception as e:
        log.warning("[election] 推送失败: %s", e)


async def _handle_promise_query(ws: WebSocket, msg: dict) -> None:
    """C→S：拉取当前承诺池快照（按当前任期 / candidate=player）。"""
    game_day = int(msg.get("game_day", 0))
    term = election_store.ensure_term_active(game_day)
    active = promise_store.list_active_for_term(int(term["term_id"]), "player")
    history = promise_store.list_all_for_term(int(term["term_id"]), "player")

    # 给每条 promise 注入 quest 元信息（title/desc/kind/requires），
    # 以便客户端面板直接展示，无需再查 quest defs
    defs = quest_engine.defs
    def _enrich(promise: dict) -> dict:
        qid = promise.get("quest_id", "")
        q = defs.get(qid, {})
        out = dict(promise)
        out["quest_title"] = q.get("title", "")
        out["quest_desc"] = q.get("desc", "")
        out["quest_kind"] = q.get("kind", "")
        out["quest_requires"] = q.get("requires", {})
        return out

    enriched_active = [_enrich(p) for p in active]
    enriched_history = [_enrich(p) for p in history]

    payload = {
        "type": "promise_state",
        "ok": True,
        "term_id": int(term["term_id"]),
        "active": enriched_active,
        "history": enriched_history,
        "active_count": len(active),
        "max_count": 5,
    }
    await ws.send_text(json.dumps(payload, ensure_ascii=False, default=str))


async def _broadcast_promise_state(ws: WebSocket, game_day: int) -> None:
    """承诺状态变化后主动推（chat / gift / quest 接受 / 兑现 时调）。"""
    try:
        await _handle_promise_query(ws, {"game_day": game_day})
    except Exception as e:
        log.debug("[promise] 推送失败: %s", e)


async def _handle_debate_start(ws: WebSocket, msg: dict) -> None:
    """C→S：辩论日开场，拉取 3 道辩题（含 4 象限选项）。

    入参：{"type":"debate_start","game_day":N}
    出参：{"type":"debate_questions", term_id, questions:[...], stance_labels, done}
    """
    game_day = int(msg.get("game_day", 0))
    term = election_store.ensure_term_active(game_day)
    term_id = int(term["term_id"])
    questions = debate_manager.pick_questions(term, n=3)
    payload = {
        "type": "debate_questions",
        "ok": True,
        "term_id": term_id,
        "opponent_id": term["opponent_id"],
        "questions": questions,
        "stance_labels": debate_manager.stance_labels,
        "already_done": debate_manager.has_debated(term_id),
    }
    await ws.send_text(json.dumps(payload, ensure_ascii=False))


async def _handle_debate_rebut(ws: WebSocket, msg: dict) -> None:
    """C→S：玩家选完某题，对手即时反驳。

    入参：{"type":"debate_rebut","game_day":N,"question":str,"stance":str,"answer_text":str,"question_index":int}
    出参：{"type":"debate_rebuttal", question_index, text}
    """
    game_day = int(msg.get("game_day", 0))
    term = election_store.ensure_term_active(game_day)
    question = msg.get("question", "")
    stance = msg.get("stance", "")
    answer_text = msg.get("answer_text", "")
    q_index = int(msg.get("question_index", 0))
    try:
        text = await debate_manager.rebut(term, question, stance, answer_text)
    except Exception as e:
        log.warning("[debate] 反驳失败: %s", e)
        text = "哼，说得轻巧。"
    await ws.send_text(json.dumps({
        "type": "debate_rebuttal",
        "ok": True,
        "question_index": q_index,
        "text": text,
    }, ensure_ascii=False))


async def _handle_debate_submit(ws: WebSocket, msg: dict) -> None:
    """C→S：玩家提交全部答案，结算辩论分。

    入参：{"type":"debate_submit","game_day":N,"answers":{"0":"radical",...}}
    出参：{"type":"debate_result", scores, ...} + 随后推一次 election_state
    """
    game_day = int(msg.get("game_day", 0))
    term = election_store.ensure_term_active(game_day)
    raw_answers = msg.get("answers", {}) or {}
    # 键可能是字符串，转 int
    answers: dict = {}
    for k, v in raw_answers.items():
        try:
            answers[int(k)] = str(v)
        except Exception:
            continue
    try:
        result = debate_manager.score_and_persist(term, answers)
    except Exception as e:
        log.warning("[debate] 评分失败: %s", e)
        await _send_error(ws, f"辩论评分失败: {e}")
        return

    # 计算给玩家带来的总 debate 加权（汇总各 voter 子项），方便 UI 展示
    player_total = 0.0
    opponent_total = 0.0
    for voter in election_store.voters_of(term):
        _, sub_p = election_store.compute_weight(voter, "player", term)
        _, sub_o = election_store.compute_weight(voter, term["opponent_id"], term)
        player_total += sub_p.get("debate", 0.0)
        opponent_total += sub_o.get("debate", 0.0)

    await ws.send_text(json.dumps({
        "type": "debate_result",
        "ok": True,
        "term_id": int(term["term_id"]),
        "player_scores": result["player_scores"],
        "opponent_scores": result["opponent_scores"],
        "player_debate_total": round(player_total, 1),
        "opponent_debate_total": round(opponent_total, 1),
        "stance_labels": debate_manager.stance_labels,
    }, ensure_ascii=False))

    # 辩论改变权重 → 推一次最新选举状态
    await _broadcast_election_state(ws, game_day)


# ---------- 任期权力点（D9）----------

async def _handle_power_query(ws: WebSocket, msg: dict) -> None:
    """C→S：拉取当前权力点状态 + 可用行动 + 可选目标列表。

    出参：{type:power_state, incumbent, power, power_max, actions:[...], targets:[...]}
    """
    game_day = int(msg.get("game_day", 0))
    term = election_store.ensure_term_active(game_day)
    term_id = int(term["term_id"])
    incumbent = election_store.is_incumbent(term_id, "player")
    power = 0
    power_max = 0
    if incumbent:
        power = election_store.refresh_power_points(term_id, "player", game_day)
        st = election_store.get_candidate_state(term_id, "player")
        power_max = int(st.get("power_points_max", 3)) if st else 3

    actions = [
        {"id": k, "label": v["label"], "cost": v["cost"],
         "need_target": v["need_target"], "desc": v["desc"]}
        for k, v in POWER_ACTIONS.items()
    ]
    targets = [
        {"npc_id": v, "name": personas.get(v, {}).get("name", v)}
        for v in election_store.voters_of(term)
    ]
    await ws.send_text(json.dumps({
        "type": "power_state",
        "ok": True,
        "term_id": term_id,
        "incumbent": incumbent,
        "power": power,
        "power_max": power_max,
        "actions": actions,
        "targets": targets,
    }, ensure_ascii=False))


async def _handle_power_action(ws: WebSocket, msg: dict) -> None:
    """C→S：执行一个权力行动。

    入参：{type:power_action, game_day, action, target_id?}
    出参：{type:power_result, ...} + 随后推 power_state / election_state
    """
    game_day = int(msg.get("game_day", 0))
    action = str(msg.get("action", ""))
    target_id = str(msg.get("target_id", ""))
    term = election_store.ensure_term_active(game_day)
    try:
        result = await power_manager.perform(term, game_day, action, target_id)
    except Exception as e:
        log.warning("[power] 行动失败: %s", e)
        await _send_error(ws, f"权力行动失败: {e}")
        return

    await ws.send_text(json.dumps({
        "type": "power_result",
        **result,
    }, ensure_ascii=False))

    if result.get("ok"):
        # 行动改变好感 / 事件 → 刷新选举状态 + 权力点
        await _broadcast_election_state(ws, game_day)
        await _handle_power_query(ws, {"game_day": game_day})
        # 把受影响 NPC 的好感变化也推给客户端（让头顶飘字 / 状态更新）
        for a in result.get("affected", []):
            await ws.send_text(json.dumps({
                "type": "reply",
                "animal_id": a.get("npc_id", ""),
                "text": "",
                "affection": {
                    "value": a.get("affection", 0),
                    "level": a.get("level", "neutral"),
                    "delta": a.get("delta", 0),
                },
                "silent": True,
                "ok": True,
            }, ensure_ascii=False))


async def _handle_debug_grant_power(ws: WebSocket, msg: dict) -> None:
    """调试：把玩家设为现任并补满权力点（不必赢选举即可测试）。"""
    game_day = int(msg.get("game_day", 0))
    term = election_store.ensure_term_active(game_day)
    term_id = int(term["term_id"])
    election_store.set_incumbent(term_id, "player")
    # 强制重置补满
    with_conn_reset = election_store.get_candidate_state(term_id, "player")
    if with_conn_reset is not None:
        # last_power_day 设为 -1 触发补满
        from db import get_conn as _gc
        with _gc() as conn:
            conn.execute(
                "UPDATE candidate_state SET last_power_day = -1 WHERE term_id = ? AND candidate_id = 'player'",
                (term_id,),
            )
    election_store.refresh_power_points(term_id, "player", game_day)
    log.info("[power] DEBUG 授权玩家现任 term=%d", term_id)
    await _handle_power_query(ws, {"game_day": game_day})
    await _broadcast_election_state(ws, game_day)


async def _run_opponent_daily(term: dict, game_day: int, ws: WebSocket) -> None:
    """对手 NPC 当日动作（异步）：纲领懒生 + 多个动作（visit/promise/smear）。

    动作数随任期推进 + 落后幅度增加；每个动作推送 opponent_action 消息给客户端。
    """
    try:
        await opponent_ai.ensure_platform(term)
        # 传入当前比分，供追赶系数判断落后幅度
        view = election_store.get_current_term_view(game_day)
        scores = view.get("scores", {})
        player_score = float(scores.get("player", 0.0))
        opponent_score = float(scores.get(term["opponent_id"], 0.0))
        actions = await opponent_ai.run_daily_actions(
            term, game_day, player_score=player_score, opponent_score=opponent_score
        )
        for action in actions:
            await ws.send_text(json.dumps({
                "type": "opponent_action",
                "ok": True,
                "term_id": action["term_id"],
                "game_day": action["game_day"],
                "candidate_id": action["candidate_id"],
                "action_type": action["action_type"],
                "target_npc": action["target_npc"],
                "text": action["llm_text"],
            }, ensure_ascii=False))
        # 行动后比分变化 → 推一次最新状态刷新 HUD
        if actions:
            await _broadcast_election_state(ws, game_day)
    except Exception as e:
        log.warning("[opponent_ai] daily action 失败: %s", e)


async def _handle_time_tick(msg: dict, ws: WebSocket) -> None:
    """接收客户端游戏时间 tick，每日 22:00 触发反思 + 9-18 激活意图。

    消息格式：{"type": "time_tick", "game_day": N, "game_hour": H}
    """
    global _last_reflect_day, _last_election_recompute_day, _last_opponent_action_day, _last_known_game_day
    game_day = int(msg.get("game_day", -1))
    game_hour = int(msg.get("game_hour", 0))

    if game_day < 0:
        return

    _last_known_game_day = game_day

    # 激活当日待执行意图（每小时检查一次）
    await _activate_pending_intents(game_day, game_hour, ws)

    # 22:00+ 且是新的一天 → 触发反思（非阻塞）
    if game_hour >= 22 and game_day > _last_reflect_day:
        _last_reflect_day = game_day
        asyncio.create_task(manager.run_all_daily_reflections(game_day, intent_store))

    # 07:00+ → 对手 NPC 当日动作（仅 campaign 阶段，D6/D7 不动）
    if game_hour >= 7 and game_day > _last_opponent_action_day:
        try:
            term = election_store.ensure_term_active(game_day)
            day_idx = election_store.day_index_in_term(term, game_day)
            phase = election_store.phase_of(day_idx)
            if phase == "campaign":
                _last_opponent_action_day = game_day
                asyncio.create_task(_run_opponent_daily(term, game_day, ws))
        except Exception as e:
            log.warning("[opponent_ai] 触发失败: %s", e)

    # 选举权重重算：每日 22:00+ 重算一次（与反思同时机），并推送状态
    if game_hour >= 22 and game_day > _last_election_recompute_day:
        _last_election_recompute_day = game_day
        try:
            term = election_store.ensure_term_active(game_day)
            election_store.recompute_and_persist_weights(term, game_day)
            log.info("[election] day=%d 权重重算完毕 term=%d", game_day, term["term_id"])

            # 若到达投票日（day_index >= VOTE_DAY_INDEX）→ 自动结算 + 开新任期
            settle_info = election_store.settle_term_if_due(game_day)
            if settle_info is not None:
                # 推送投票结果（前端用于触发投票日演出）
                try:
                    await ws.send_text(json.dumps({
                        "type": "election_result",
                        "ok": True,
                        **settle_info,
                    }, ensure_ascii=False))
                except Exception as e:
                    log.warning("[election] 推送结算失败: %s", e)
                # 写一条世界事件
                winner = settle_info["winner_id"]
                world_store.add(
                    actor=winner,
                    description=(
                        f"第 {settle_info['settled_term_id']} 届镇长选举结束，"
                        f"{'玩家' if winner == 'player' else winner} 当选 "
                        f"(票数 {settle_info['votes']})"
                    ),
                )

            await _broadcast_election_state(ws, game_day)
        except Exception as e:
            log.warning("[election] 重算失败: %s", e)


async def _activate_pending_intents(game_day: int, game_hour: int, ws: WebSocket) -> None:
    """检查待执行意图，逐条推送 npc_intent 消息给客户端。"""
    pending = intent_store.pending(game_day, game_hour)
    if not pending:
        return
    for entry in pending:
        intent_store.mark_consumed(entry.id)
        log.info(
            "[intent] 激活 %s→%s day=%d: %s",
            entry.animal_id, entry.target_id or "?", game_day, entry.intent_text[:30],
        )
        try:
            await ws.send_text(json.dumps({
                "type": "npc_intent",
                "initiator_id": entry.animal_id,
                "target_id": entry.target_id,
                "intent_text": entry.intent_text,
            }, ensure_ascii=False))
        except Exception as e:
            log.warning("[intent] 推送失败: %s", e)


# ---------- Debug HTTP ----------

@app.post("/debug/reflect/{animal_id}/{day}")
async def debug_reflect(animal_id: str, day: int):
    """强制触发指定 NPC + 游戏日的反思（force=True，跳过"今日已反思"检查）。"""
    agent = manager.get(animal_id)
    if agent is None:
        return {"ok": False, "error": f"未知 animal_id: {animal_id}"}
    from reflection import run_daily_reflection as _refl_fn
    species = agent.persona.get("species", "怪物")
    npc_name_map = {a.name: aid2 for aid2, a in manager._agents.items()}
    results = await _refl_fn(
        animal_id, agent.name, species, day,
        agent.memory, agent.world, agent.affection,
        agent.reflection_store, agent.llm,
        npc_name_map=npc_name_map,
        intent_store=intent_store,
        force=True,
    )
    return {
        "ok": True,
        "animal_id": animal_id,
        "day": day,
        "count": len(results),
        "reflections": [{"content": r.content, "importance": r.importance, "tags": r.tags} for r in results],
    }


@app.post("/debug/election/force_vote")
async def debug_force_vote(game_day: int = 0):
    """强制结算当前任期，不必等到 D7。

    用法：curl -X POST 'http://127.0.0.1:8765/debug/election/force_vote?game_day=10'
    若 game_day < D7 应到的 day，函数内部把 day 调整到 day_index>=7。
    """
    term = election_store.ensure_term_active(game_day)
    needed_day = int(term["start_day"]) + 6  # day_index = 7
    use_day = max(game_day, needed_day)
    election_store.recompute_and_persist_weights(term, use_day)
    settle = election_store.settle_term_if_due(use_day)
    return {
        "ok": settle is not None,
        "settle": settle,
        "used_game_day": use_day,
    }


@app.get("/debug/election/state")
async def debug_election_state(game_day: int = 0):
    """查看当前选举完整视图（含所有 voter 的 weight breakdown）。"""
    return election_store.get_current_term_view(game_day)


async def _send_error(ws: WebSocket, message: str) -> None:
    await ws.send_text(
        json.dumps({"type": "error", "message": message}, ensure_ascii=False)
    )


# ---------- 入口 ----------
if __name__ == "__main__":
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8765"))
    log.info("listening on ws://%s:%s/ws", host, port)
    uvicorn.run(app, host=host, port=port, log_level="info")
