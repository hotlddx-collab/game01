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
manager = AgentManager(
    personas, llm, memory_store, profile_store, world_store,
    affection_store, gift_store, reflection_store,
)
log.info("加载 personas: %s", manager.all_ids())

# 记录上次触发反思的游戏日，避免同日重复触发
_last_reflect_day: int = -1


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

    animal_id = msg.get("animal_id", "")
    agent = manager.get(animal_id)
    if agent is None:
        await _send_error(ws, f"未知 animal_id: {animal_id}")
        return

    context = msg.get("context", {})

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


async def _handle_time_tick(msg: dict, ws: WebSocket) -> None:
    """接收客户端游戏时间 tick，每日 22:00 触发反思 + 9-18 激活意图。

    消息格式：{"type": "time_tick", "game_day": N, "game_hour": H}
    """
    global _last_reflect_day
    game_day = int(msg.get("game_day", -1))
    game_hour = int(msg.get("game_hour", 0))

    if game_day < 0:
        return

    # 激活当日待执行意图（每小时检查一次）
    await _activate_pending_intents(game_day, game_hour, ws)

    # 22:00+ 且是新的一天 → 触发反思（非阻塞）
    if game_hour >= 22 and game_day > _last_reflect_day:
        _last_reflect_day = game_day
        asyncio.create_task(manager.run_all_daily_reflections(game_day, intent_store))


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
