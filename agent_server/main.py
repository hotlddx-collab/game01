"""FastAPI + WebSocket 主服务。

启动: python main.py
"""
from __future__ import annotations

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
manager = AgentManager(personas, llm, memory_store, profile_store, world_store)
log.info("加载 personas: %s", manager.all_ids())


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

    # NPC↔NPC 对话（特殊：需要两个 agent，不走 animal_id 单查）
    if msg_type == "npc_chat":
        await _handle_npc_chat(ws, msg)
        return

    animal_id = msg.get("animal_id", "")
    agent = manager.get(animal_id)
    if agent is None:
        await _send_error(ws, f"未知 animal_id: {animal_id}")
        return

    context = msg.get("context", {})

    try:
        if msg_type == "greet":
            text = await agent.greet(context)
        elif msg_type == "chat":
            user_text = msg.get("user_text", "")
            if not user_text.strip():
                await _send_error(ws, "user_text 为空")
                return
            text = await agent.reply(user_text, context)
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

    await ws.send_text(
        json.dumps(
            {"type": "reply", "animal_id": animal_id, "text": text, "ok": True},
            ensure_ascii=False,
        )
    )


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
    try:
        result = await manager.trigger_npc_chat(speaker_id, listener_id, context)
    except Exception as e:
        log.exception("npc_chat LLM 失败")
        await _send_error(ws, f"LLM 错误: {e}")
        return
    if result is None:
        await _send_error(ws, "未知 speaker 或 listener")
        return
    await ws.send_text(
        json.dumps({"type": "npc_chat_reply", **result, "ok": True}, ensure_ascii=False)
    )


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
