"""FastAPI + WebSocket 主服务。

启动: python main.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Optional

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
import items
from affection import AffectionStore
from gifts import GiftStore
from reflection import ReflectionStore, IntentStore
from election import ElectionStore, TERM_DAYS, VOTE_DAY_INDEX, LOYALTY_MAP
import belief
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
from mood import MoodStore
mood_store = MoodStore()
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
    mood_store=mood_store,
)
log.info("加载 personas: %s", manager.all_ids())

# 八卦系统（活社会核心）
from rumor import RumorStore, RumorManager, sentiment_label
rumor_store = RumorStore()
rumor_manager = RumorManager(
    rumor_store, llm, world_store,
    name_of=lambda aid: (manager.get(aid).name if manager.get(aid) else aid),
    persona_of=lambda aid: (manager.get(aid).persona if manager.get(aid) else {}),
    mood_store=mood_store,
)
log.info("[rumor] RumorManager 就绪")


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
            _s = session_ctx.get()
            _kd = _s.last_known_game_day if _s else -1
            day = _kd if _kd >= 0 else int(term.get("start_day", 0))
            # deadline = 任期结束日（投票日结算）
            deadline = int(term["start_day"]) + (VOTE_DAY_INDEX - 1)
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
        _s = session_ctx.get()
        _kd = _s.last_known_game_day if _s else -1
        day = _kd if _kd >= 0 else 0
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

# 危机调解系统（镇长政务玩法）
from crisis import CrisisManager
crisis_manager = CrisisManager(
    election_store=election_store,
    affection_store=affection_store,
    world_store=world_store,
    personas=personas,
    memory_store=memory_store,
    llm=llm,
)
log.info("[crisis] CrisisManager 就绪 危机数=%d", len(crisis_manager.defs))

# 镇长政务任务系统（现任镇长专属：指挥 NPC 干活）
from mayor_tasks import MayorTaskManager
mayor_task_manager = MayorTaskManager(
    election_store=election_store,
    affection_store=affection_store,
    mood_store=mood_store,
    world_store=world_store,
    personas=personas,
    npc_ids=list(manager.all_ids()),
    llm=llm,
)
log.info("[mayor_task] MayorTaskManager 就绪")


# 每个玩家世界的运行时状态（DB 路由 + 每日去重计数器）现随 Session 走，
# 见 session.py。之前的模块级 _last_* 全局已移入 Session 实例。
import session as session_mod
from session import session_ctx

session_registry = session_mod.SessionRegistry()


def _known_day() -> int:
    """当前会话记录的最近 game_day（无则 -1）。"""
    s = session_ctx.get()
    return s.last_known_game_day if s else -1


# ---------- HTTP 健康检查 ----------
@app.get("/")
async def root():
    return {"status": "ok", "animals": manager.all_ids()}


# ---------- WebSocket ----------
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    # 会话隔离：客户端用 ws://host/ws?sid=UUID 上报身份，各自独立世界。
    # 无 sid（编辑器/旧客户端）沿用默认 town.db。绑定在本连接任务上下文，
    # 后续所有 await 及 asyncio.create_task 派生的后台任务都继承该会话。
    sid = ws.query_params.get("sid", "")
    sess = session_registry.get_or_create(sid)
    session_mod.bind(sess)
    log.info("client connected sid=%s db=%s", sid or "<default>", sess.db_path.name)
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

    # 拾取上报（NPC 或玩家捡到地上道具）→ 写世界事件 + NPC forage 记忆
    if msg_type == "pickup_report":
        await _handle_pickup_report(ws, msg)
        return

    # 八卦：打听 / 放话 / 辟谣
    if msg_type == "rumor_inquire":
        await _handle_rumor_inquire(ws, msg)
        return
    if msg_type == "rumor_spread":
        await _handle_rumor_spread(ws, msg)
        return
    if msg_type == "rumor_debunk":
        await _handle_rumor_debunk(ws, msg)
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
    if msg_type == "debug_opponent_action":
        await _handle_debug_opponent_action(ws, msg)
        return

    # 危机调解：查询当前危机 / 提交调解方案 / 调试触发
    if msg_type == "crisis_query":
        await _handle_crisis_query(ws, msg)
        return
    if msg_type == "crisis_resolve":
        await _handle_crisis_resolve(ws, msg)
        return
    if msg_type == "debug_spawn_crisis":
        await _handle_debug_spawn_crisis(ws, msg)
        return

    # 镇长政务任务：查询当前任务 / 指派 NPC 执行 / 调试刷新
    if msg_type == "mayor_task_query":
        await _handle_mayor_task_query(ws, msg)
        return
    if msg_type == "mayor_task_assign":
        await _handle_mayor_task_assign(ws, msg)
        return
    if msg_type == "debug_spawn_mayor_task":
        await _handle_debug_spawn_mayor_task(ws, msg)
        return
    if msg_type == "debug_make_mayor":
        await _handle_debug_make_mayor(ws, msg)
        return

    animal_id = msg.get("animal_id", "")
    agent = manager.get(animal_id)
    if agent is None:
        await _send_error(ws, f"未知 animal_id: {animal_id}")
        return

    context = msg.get("context", {})
    # 顺手更新 game_day（promise 钩子用），记在当前会话
    try:
        gd = int(context.get("game_day", -1))
        if gd >= 0:
            _s = session_ctx.get()
            if _s:
                _s.last_known_game_day = gd
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

    # 心情推动：由好感 delta + 互动类型折算，回包带当前心情供前端头顶显示
    try:
        _gd = int(context.get("game_day", -1))
        _aff_d = int(result.get("affection", {}).get("delta", 0) or 0)
        if msg_type == "greet":
            _md = _aff_d + 1
        elif msg_type == "chat":
            _md = _aff_d * 2 if _aff_d != 0 else 1
        elif msg_type == "gift":
            _md = _aff_d * 2
        else:
            _md = 0
        payload["mood"] = mood_store.adjust(animal_id, _md, _gd)
    except Exception as e:
        log.debug("[mood] 推动失败: %s", e)

    # 八卦生成：玩家显著言行 → 以玩家为主角的话题，见证的 NPC 成为初始知情者
    try:
        _npc_name = agent.name
        _gd2 = int(context.get("game_day", -1))
        _gift_d = int(result.get("gift", {}).get("delta", 0) or 0) if "gift" in result else 0
        if msg_type == "gift" and _gift_d >= 4:
            rumor_manager.generate(
                "player", f"那位旅人给{_npc_name}送了挺贵重的东西，出手真大方",
                sentiment="praise", origin="auto", game_day=_gd2, initial_knowers=[animal_id])
        elif msg_type == "gift" and _gift_d <= -2:
            rumor_manager.generate(
                "player", f"那位旅人送{_npc_name}的东西，人家压根不待见",
                sentiment="smear", origin="auto", game_day=_gd2, initial_knowers=[animal_id])
        elif msg_type == "chat" and _aff_d <= -3:
            rumor_manager.generate(
                "player", f"那位旅人跟{_npc_name}说话冲得很，把人惹毛了",
                sentiment="smear", origin="auto", game_day=_gd2, initial_knowers=[animal_id])
    except Exception as e:
        log.debug("[rumor] 生成失败: %s", e)

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

    # 八卦：speaker 手里若有热门话题，注入让 ta 闲聊时带出来（会变味）
    gossip_item = None
    try:
        gossip_item = rumor_manager.pick_gossip_for(speaker_id)
        if gossip_item:
            context = dict(context)
            context["gossip"] = {
                "subject": rumor_manager.subject_label(gossip_item["rumor"].subject_id),
                "version": gossip_item["version"],
            }
    except Exception as e:
        log.debug("[rumor] pick_gossip 失败: %s", e)

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

    # 八卦传播：listener 学到 speaker 带出来的话题（变味），并处理"传到当事人"后果
    if gossip_item:
        try:
            game_day = int(context.get("game_day", _known_day()))
            spread = await rumor_manager.propagate(
                gossip_item["rumor"].id, speaker_id, listener_id, game_day)
            if spread and spread.get("reached_subject"):
                await _apply_rumor_consequence(ws, spread, listener_id, game_day)
            if spread:
                subj = spread.get("subject_id", "")
                # 传播链逐人结算：listener 以 speaker 为传谣者再做一次信念判定
                # （每人每条只判一次，故谣言影响上限 = 全镇人数，不会无限膨胀）
                r = rumor_store.get(gossip_item["rumor"].id)
                ev = _judge_and_record(
                    gossip_item["rumor"].id, listener_id, speaker_id, subj,
                    r.sentiment if r else "", game_day) if r else []
                if ev and (subj in set(manager.all_ids()) or subj == "player"):
                    await _broadcast_election_state(ws, game_day, belief_events=ev)
        except Exception as e:
            log.debug("[rumor] 传播失败: %s", e)


async def _apply_rumor_consequence(ws: WebSocket, spread: dict, subject_id: str, game_day: int) -> None:
    """话题传到当事人耳朵：影响其心情，并（若源自玩家）牵动对玩家的好感。"""
    sentiment = spread.get("sentiment", "neutral")
    r = rumor_store.get(spread.get("rumor_id", 0))
    origin = r.origin if r else ""
    mood_delta = 0
    aff_delta = 0
    if sentiment == "praise":
        mood_delta = 10
        if origin == "player":
            aff_delta = 2
    elif sentiment == "smear":
        mood_delta = -15
        if origin == "player":
            aff_delta = -4   # 发现是玩家散布的坏话 → 记恨
    if mood_delta == 0 and aff_delta == 0:
        return
    mood_snap = mood_store.adjust(subject_id, mood_delta, game_day)
    aff = affection_store.adjust(subject_id, aff_delta) if aff_delta else affection_store.snapshot(subject_id)
    await ws.send_text(json.dumps({
        "type": "reply",
        "animal_id": subject_id,
        "text": "",
        "affection": {"value": aff.get("value", 0), "level": aff.get("level", "neutral"),
                      "delta": aff.get("delta", 0)},
        "mood": mood_snap,
        "silent": True,
        "ok": True,
    }, ensure_ascii=False))
    log.info("[rumor] consequence subject=%s sent=%s mood%+d aff%+d", subject_id, sentiment, mood_delta, aff_delta)


async def _handle_rumor_inquire(ws: WebSocket, msg: dict) -> None:
    """玩家向 NPC 打听：ta 把知道的最热话题用自己的口吻讲给玩家。"""
    animal_id = msg.get("animal_id", "")
    agent = manager.get(animal_id)
    if agent is None:
        await _send_error(ws, "未知 animal_id")
        return
    known = rumor_store.known_by(animal_id, min_heat=1)
    if not known:
        await ws.send_text(json.dumps({
            "type": "rumor_reply", "animal_id": animal_id,
            "text": "最近？没听说啥新鲜事儿。", "has_rumor": False, "ok": True,
        }, ensure_ascii=False))
        return
    top = known[0]
    subject = rumor_manager.subject_label(top["rumor"].subject_id)
    prompt = (
        f"你是{agent.name}。有人凑过来问你「最近镇上有什么新鲜事」。\n"
        f"你正好听说了关于{subject}的一件事：「{top['version']}」\n"
        "请用你自己的口吻，压低声音八卦一句（20 字内），把这事透露给对方。只输出这句话。"
    )
    try:
        line = await llm.chat([{"role": "user", "content": prompt}], max_tokens=60, temperature=0.9)
        line = line.strip().strip("「」\"'").splitlines()[0][:50]
    except Exception:
        line = top["version"]
    await ws.send_text(json.dumps({
        "type": "rumor_reply", "animal_id": animal_id, "text": line,
        "has_rumor": True, "rumor_id": top["rumor"].id,
        "subject_id": top["rumor"].subject_id, "sentiment": top["rumor"].sentiment,
        "ok": True,
    }, ensure_ascii=False))


# 玩家放话情感推断词表（比选举 event 用的更宽，覆盖日常褒贬）
_RUMOR_SMEAR_WORDS = (
    "缺斤少两", "坏", "差", "骗", "假", "黑心", "丑", "贪", "懒", "脏", "偷", "抢",
    "恶", "烂", "臭", "无能", "自私", "虚伪", "欺", "骂", "吵", "破坏", "失约",
    "麻烦", "丑闻", "醉", "难吃", "缺德", "坑", "不行", "没本事", "小气", "抠",
    "作弊", "偷懒", "撒谎", "背叛", "阴险", "耍赖",
)
_RUMOR_PRAISE_WORDS = (
    "好", "棒", "优秀", "善良", "慷慨", "能干", "靠谱", "厉害", "帮", "救", "支持",
    "诚实", "公正", "热心", "大方", "可靠", "有本事", "用心", "漂亮", "美味",
    "好吃", "勤快", "仗义", "靠得住", "为大家", "负责", "实在", "亲切",
)


_RUMOR_NEGATORS = ("不", "没", "别", "无", "非", "未", "算不上", "称不上")


def _rumor_polarity(content: str) -> int:
    """按词表计分：正数偏褒、负数偏贬、0 无倾向。

    改用计分而非「命中即定性」，避免贬义句里出现「好/帮」等高频褒义字时
    被误判或互相抵消（旧实现 neg and not pos 会退回 neutral → 造谣完全失效）。
    褒义词前若紧跟否定词（如「不好」「没本事」），极性翻转记为贬义。
    """
    score = 0
    for w in _RUMOR_SMEAR_WORDS:
        idx = content.find(w)
        while idx >= 0:
            score -= 2
            idx = content.find(w, idx + 1)
    for w in _RUMOR_PRAISE_WORDS:
        idx = content.find(w)
        while idx >= 0:
            prefix = content[max(0, idx - 2):idx]
            if any(n in prefix for n in _RUMOR_NEGATORS):
                score -= 2   # 「不好」「没能干」→ 实为贬义
            else:
                score += 2
            idx = content.find(w, idx + 1)
    return score


def _infer_rumor(content: str, subject_id: str, sentiment: str, force_smear: bool = False):
    """从玩家自由文本推断话题主角 + 情感（客户端只传自由文本时用）。"""
    # 主角：内容提到某 NPC 名字 → 话题针对该 NPC；否则维持默认（多为 player）
    if subject_id in ("", "player"):
        matched = False
        for aid in manager.all_ids():
            ag = manager.get(aid)
            if ag and ag.name and ag.name in content:
                subject_id = aid
                matched = True
                break
        if not matched and force_smear:
            # 玩家造谣却没点名（如「他昨晚偷偷见了税吏」）→ 默认指向竞选对手，
            # 否则 subject 停在 player，抹黑会打到玩家自己身上
            term = election_store.get_active_term()
            opp = term.get("opponent_id") if term else ""
            if opp:
                subject_id = opp
    # 情感：仅当客户端未显式指定（neutral）时按褒贬计分判定
    if sentiment == "neutral":
        pol = _rumor_polarity(content)
        if pol < 0:
            sentiment = "smear"
        elif pol > 0:
            sentiment = "praise"
        elif force_smear:
            # 玩家主动用「造谣」发出、文本无明显褒贬词（如「他昨晚偷偷见了税吏」）
            # 也应当作抹黑生效，否则 neutral 会被后续全链过滤掉 → 零影响
            sentiment = "smear"
    return subject_id, sentiment


def _judge_and_record(rumor_id: int, listener_id: str, source_id: str,
                      subject_id: str, sentiment: str, game_day: int) -> list:
    """对一位听者做信念判定并落库（每人每条只判一次）。

    返回归因事件列表，供前端在选情分右侧飘字：
      [{"kind": "believed"/"rejected", "listener": 名字, "source": 名字,
        "subject_id": ..., "sentiment": ...}]
    已判定过则返回空列表 → 重复造谣不再产生任何影响。
    """
    if sentiment not in ("smear", "praise"):
        return []
    if rumor_store.get_belief(rumor_id, listener_id) is not None:
        return []   # 判定即锁定，不重复结算
    verdict = belief.judge(
        listener_id, source_id, subject_id, sentiment,
        affection_store, LOYALTY_MAP)
    state = "believed" if verdict["believe"] else "rejected"
    fresh = rumor_store.set_belief(
        rumor_id, listener_id, state, source_id=source_id,
        score=verdict["score"], day=game_day)
    if not fresh:
        return []
    if state == "rejected" and source_id == "player":
        # 不信 → 玩家造谣有一点社交成本
        affection_store.adjust(listener_id, belief.REJECT_AFF_PENALTY)
    lname = rumor_manager.subject_label(listener_id)
    sname = "你" if source_id == "player" else rumor_manager.subject_label(source_id)
    log.info("[rumor] belief r=%s %s<-%s %s score=%.1f (%s)",
             rumor_id, listener_id, source_id, state, verdict["score"], verdict["reason"])
    return [{
        "kind": state,
        "listener": lname,
        "source": sname,
        "reason": verdict["reason"],
        "subject_id": subject_id,
        "sentiment": sentiment,
    }]


async def _handle_rumor_spread(ws: WebSocket, msg: dict) -> None:
    """玩家放话：把一句话灌给某 NPC，成为新话题的初始知情者（可真可假）。

    UI 只给自由文本，故服务端从内容里推断：
      - 主角：提到某 NPC 名字 → 话题主角是该 NPC（可针对候选人）；否则默认玩家自己。
      - 情感：褒/贬词判 praise / smear，用于影响该主角的选举 event 分。
    """
    animal_id = msg.get("animal_id", "")
    subject_id = str(msg.get("subject_id", "")) or "player"
    content = str(msg.get("content", "")).strip()
    sentiment = str(msg.get("sentiment", "neutral"))
    truth = int(msg.get("truth", 0))
    game_day = int(msg.get("game_day", _known_day()))
    agent = manager.get(animal_id)
    if agent is None or not content:
        await _send_error(ws, "rumor_spread 缺少 animal_id 或 content")
        return
    subject_id, sentiment = _infer_rumor(content, subject_id, sentiment, force_smear=True)
    # 只让「当面这一位」成为初始知情者，并当场做一次信念判定。
    # 信 → 才影响选情且永久锁定（重复造同一话题不再叠加）；不信 → 掉一点对玩家好感。
    # 后续影响靠 NPC 之间闲聊逐人扩散（每人同样只判一次）。
    rid = rumor_manager.generate(
        subject_id, content, sentiment=sentiment, truth=truth,
        origin="player", game_day=game_day, initial_knowers=[animal_id], heat=60)
    belief_events = _judge_and_record(
        rid, animal_id, "player", subject_id, sentiment, game_day)
    # NPC 当面反应：语气贴合信 / 不信 / 早已听过
    if not belief_events:
        stance = "你早就听过这个说法了，不觉得新鲜，敷衍两句。"
    elif belief_events[0]["kind"] == "believed":
        stance = "你信了这个说法，表现出吃惊或恍然大悟。"
    else:
        stance = "你不太信这个说法，表现出怀疑甚至有点反感。"
    prompt = (
        f"你是{agent.name}。有人悄悄跟你说了个小道消息：「{content}」\n"
        f"{stance}\n"
        "请用你自己的口吻回一句（15 字内）。只输出这句话。"
    )
    try:
        line = await llm.chat([{"role": "user", "content": prompt}], max_tokens=50, temperature=0.9)
        line = line.strip().strip("「」\"'").splitlines()[0][:40]
    except Exception:
        line = "哦？还有这事？"
    await ws.send_text(json.dumps({
        "type": "rumor_reply", "animal_id": animal_id, "text": line,
        "spread_ok": True, "rumor_id": rid, "ok": True,
        "belief_events": belief_events,
    }, ensure_ascii=False))
    # 只有「有人新相信了」才可能改变选情 → 重算推送
    if belief_events and (subject_id in set(manager.all_ids()) or subject_id == "player"):
        try:
            await _broadcast_election_state(ws, game_day, belief_events=belief_events)
        except Exception as e:
            log.debug("[election] 散谣后推送失败: %s", e)


async def _handle_rumor_debunk(ws: WebSocket, msg: dict) -> None:
    """玩家辟谣：给某 NPC 澄清 → 大幅降热度，冷透则标 debunked。"""
    animal_id = msg.get("animal_id", "")
    rumor_id = int(msg.get("rumor_id", 0))
    agent = manager.get(animal_id)
    r = rumor_store.get(rumor_id)
    if agent is None or r is None:
        await _send_error(ws, "rumor_debunk 缺少有效 animal_id / rumor_id")
        return
    new_heat = rumor_store.adjust_heat(rumor_id, -35)
    if new_heat <= 0:
        rumor_store.set_status(rumor_id, "debunked")
    prompt = (
        f"你是{agent.name}。之前你听说过「{r.content}」，现在有人郑重跟你澄清这是谣传。\n"
        "请用你自己的口吻回一句（15 字内），表现被澄清后的反应。只输出这句话。"
    )
    try:
        line = await llm.chat([{"role": "user", "content": prompt}], max_tokens=50, temperature=0.85)
        line = line.strip().strip("「」\"'").splitlines()[0][:40]
    except Exception:
        line = "原来是误会啊……"
    await ws.send_text(json.dumps({
        "type": "rumor_reply", "animal_id": animal_id, "text": line,
        "debunk_ok": True, "rumor_id": rumor_id, "heat": new_heat, "ok": True,
    }, ensure_ascii=False))
    # 辟谣降热度 → 该候选人 event 分回升，立刻推送刷新 HUD
    if r.subject_id in set(manager.all_ids()) or r.subject_id == "player":
        try:
            await _broadcast_election_state(ws, int(msg.get("game_day", _known_day())))
        except Exception as e:
            log.debug("[election] 辟谣后推送失败: %s", e)


async def _handle_pickup_report(ws: WebSocket, msg: dict) -> None:
    """有人（NPC 或玩家）捡到了地上的道具。
    写一条世界事件（供八卦引用）；若拾取者是 NPC，再写它自己的 forage 记忆。"""
    actor_id = msg.get("actor_id", "")
    item_id = msg.get("item_id", "")
    if not actor_id or not item_id:
        await _send_error(ws, "pickup_report 缺 actor_id/item_id")
        return

    item_def = items.get(item_id)
    item_name = item_def.name if item_def else item_id

    if actor_id == "player":
        actor_name = "玩家"
    else:
        _a = manager.get(actor_id)
        actor_name = _a.name if _a else actor_id

    world_store.add(
        actor=actor_id,
        description=f"{actor_name}在镇上捡到了一个{item_name}",
    )

    # NPC 拾取 → 写它自己的记忆 + 加进 forage 库存（可被玩家索要）
    if actor_id != "player":
        actor = manager.get(actor_id)
        if actor is not None:
            actor.memory.add(
                actor_id,
                f"我刚在镇上捡到一个{item_name}",
                type="event",
                speaker="self",
                importance=6,
            )
            actor.profile.forage_inc(actor_id, item_id, 1)

    await ws.send_text(json.dumps({"type": "ok", "context": "pickup_report"}, ensure_ascii=False))


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
    game_day = int(context.get("game_day", _known_day() if _known_day() >= 0 else 0))
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
        "term_days": TERM_DAYS,
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
    needed_day = int(term["start_day"]) + (VOTE_DAY_INDEX - 1)  # day_index=VOTE_DAY_INDEX
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


async def _broadcast_election_state(ws: WebSocket, game_day: int,
                                    belief_events: list | None = None) -> None:
    """主动推送选举状态（time_tick 跨日时调）。

    belief_events：本次分数变动的归因（谁信了/谁不信），供前端在分数右侧飘字。
    """
    try:
        view = election_store.get_current_term_view(game_day)
        payload = {"type": "election_state", **view, "ok": True}
        if belief_events:
            payload["belief_events"] = belief_events
        await ws.send_text(json.dumps(payload, ensure_ascii=False))
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


async def _handle_debug_opponent_action(ws: WebSocket, msg: dict) -> None:
    """调试：立即触发一批对手行动（绕过每日一批守卫），用于肉眼验证追赶。

    入参：{"type": "debug_opponent_action", "game_day": N}
    """
    game_day = int(msg.get("game_day", 0))
    term = election_store.ensure_term_active(game_day)
    await _run_opponent_daily(term, game_day, ws, force=True)
    await _broadcast_election_state(ws, game_day)


# ---------- 危机调解 ----------

async def _push_crisis_state(ws: WebSocket, view: Optional[dict]) -> None:
    """推送当前危机视图（view=None → 无危机）。"""
    payload = {"type": "crisis_state", "ok": True, "active": view is not None}
    if view is not None:
        payload["crisis"] = view
    await ws.send_text(json.dumps(payload, ensure_ascii=False))


async def _handle_crisis_query(ws: WebSocket, msg: dict) -> None:
    """查询当前危机；若有则确保双方说法已生成再推送。"""
    active = crisis_manager.get_active()
    if active is None:
        await _push_crisis_state(ws, None)
        return
    try:
        view = await crisis_manager.open_view(active["crisis_id"])
    except Exception as e:
        log.warning("[crisis] open_view 失败: %s", e)
        view = active
    await _push_crisis_state(ws, view)


# ---------- 镇长政务任务 ----------

async def _push_mayor_task_state(ws: WebSocket) -> None:
    view = mayor_task_manager.view()
    payload = {"type": "mayor_task_state", "ok": True, "active": view is not None}
    if view is not None:
        payload["task"] = view
    await ws.send_text(json.dumps(payload, ensure_ascii=False))


def _player_is_mayor() -> bool:
    try:
        term = election_store.get_active_term()
        return bool(term) and election_store.is_incumbent(int(term["term_id"]), "player")
    except Exception:
        return False


def _player_election_score(game_day: int) -> float:
    """玩家当前聚合选情分（即时重算），用于镇务前后对比提示。"""
    try:
        view = election_store.get_current_term_view(game_day)
        return float(view.get("scores", {}).get("player", 0.0))
    except Exception:
        return 0.0


async def _handle_mayor_task_query(ws: WebSocket, msg: dict) -> None:
    await _push_mayor_task_state(ws)


async def _handle_debug_spawn_mayor_task(ws: WebSocket, msg: dict) -> None:
    game_day = int(msg.get("game_day", _known_day()))
    if game_day < 0:
        game_day = 0
    try:
        mayor_task_manager.force_spawn(game_day)
    except Exception as e:
        log.warning("[mayor_task] debug spawn 失败: %s", e)
    await _push_mayor_task_state(ws)


async def _handle_debug_make_mayor(ws: WebSocket, msg: dict) -> None:
    """GM：让玩家直接成为当前任期现任镇长（便于测试镇务玩法）。"""
    game_day = int(msg.get("game_day", _known_day()))
    if game_day < 0:
        game_day = 0
    try:
        term = election_store.ensure_term_active(game_day)
        term_id = int(term["term_id"])
        election_store.set_incumbent(term_id, "player")
        election_store.refresh_power_points(term_id, "player", game_day)
        log.info("[gm] 玩家直升现任镇长 term=%d day=%d", term_id, game_day)
    except Exception as e:
        log.warning("[gm] make_mayor 失败: %s", e)
        await _send_error(ws, f"任命失败: {e}")
        return
    await ws.send_text(json.dumps({
        "type": "mayor_task_result",
        "ok": True,
        "gm": "make_mayor",
        "text": "你已就任镇长（GM）。按 Ctrl+M 可立即刷新一个镇务任务。",
    }, ensure_ascii=False))
    await _broadcast_election_state(ws, game_day)
    await _push_mayor_task_state(ws)


async def _handle_mayor_task_assign(ws: WebSocket, msg: dict) -> None:
    task_id = int(msg.get("task_id", 0))
    executor_id = str(msg.get("executor_id", ""))
    method = str(msg.get("method", ""))
    game_day = int(msg.get("game_day", _known_day()))
    if game_day < 0:
        game_day = 0
    game_hour = int(msg.get("game_hour", 10))
    score_before = _player_election_score(game_day)
    try:
        result = await mayor_task_manager.assign(
            task_id, executor_id, method, game_day, game_hour)
    except Exception as e:
        log.warning("[mayor_task] assign 失败: %s", e)
        await _send_error(ws, f"安排失败: {e}")
        return

    if result.get("ok") and result.get("accepted"):
        # 结算已写 world_event → 选举分即时重算，附上前后值供客户端提示
        score_after = _player_election_score(game_day)
        result["score_before"] = int(round(score_before))
        result["score_after"] = int(round(score_after))

    await ws.send_text(json.dumps({"type": "mayor_task_result", **result}, ensure_ascii=False))

    if result.get("ok") and result.get("accepted"):
        eff = result.get("effects", {}) or {}
        if eff.get("affection") is not None:
            await ws.send_text(json.dumps({
                "type": "reply",
                "animal_id": eff.get("executor_id", ""),
                "text": "",
                "affection": {
                    "value": eff.get("affection", 0),
                    "level": eff.get("level", "neutral"),
                    "delta": eff.get("aff_delta", 0),
                },
                "silent": True,
                "ok": True,
            }, ensure_ascii=False))
        # 任务已结算（选举 event 已变）→ 刷新选举分。
        # 注意：不推 mayor_task_state（否则会立即清空 HUD 追踪）；
        # 由客户端在表演「处理中 → 结果」全程接管 HUD，演完再清。
        await _broadcast_election_state(ws, game_day)


async def _handle_debug_spawn_crisis(ws: WebSocket, msg: dict) -> None:
    """调试：立即触发一个危机（忽略 min_day/cooldown）。"""
    game_day = int(msg.get("game_day", 0))
    template_id = str(msg.get("template_id", ""))
    view = crisis_manager.force_spawn(game_day, template_id, int(msg.get("game_hour", 8)))
    if view is None:
        await _send_error(ws, "危机池为空，无法触发")
        return
    try:
        view = await crisis_manager.open_view(view["crisis_id"])
    except Exception as e:
        log.warning("[crisis] open_view 失败: %s", e)
    await _push_crisis_state(ws, view)


async def _handle_crisis_resolve(ws: WebSocket, msg: dict) -> None:
    """提交调解方案：结算 + 推反应 + 刷新好感/选情。"""
    game_day = int(msg.get("game_day", 0))
    crisis_id = int(msg.get("crisis_id", 0))
    option_id = str(msg.get("option_id", ""))
    inventory = msg.get("inventory", {}) or {}
    try:
        result = await crisis_manager.resolve(crisis_id, option_id, inventory)
    except Exception as e:
        log.warning("[crisis] resolve 失败: %s", e)
        await _send_error(ws, f"调解失败: {e}")
        return

    await ws.send_text(json.dumps({"type": "crisis_result", **result}, ensure_ascii=False))

    if result.get("ok"):
        # 把受影响 NPC 的好感变化推给客户端（头顶飘字）
        for a in result.get("affected", []):
            _npc = a.get("npc_id", "")
            _mood = None
            try:
                _mood = mood_store.adjust(_npc, int(a.get("delta", 0) or 0) * 2, game_day)
            except Exception:
                pass
            await ws.send_text(json.dumps({
                "type": "reply",
                "animal_id": _npc,
                "text": "",
                "affection": {
                    "value": a.get("affection", 0),
                    "level": a.get("level", "neutral"),
                    "delta": a.get("delta", 0),
                },
                "mood": _mood,
                "silent": True,
                "ok": True,
            }, ensure_ascii=False))
        # 危机已解决 → 推空状态关闭面板 + 刷新选举（好感/事件已变）
        await _push_crisis_state(ws, None)
        # 八卦：调解结果 → 以玩家为主角的话题，受影响 NPC 为初始知情者
        try:
            _affected = result.get("affected", [])
            _knowers = [a.get("npc_id", "") for a in _affected if a.get("npc_id")]
            _sum = sum(int(a.get("delta", 0) or 0) for a in _affected)
            _title = str(result.get("title", "镇上的事"))
            if _knowers and _sum > 0:
                rumor_manager.generate(
                    "player", f"那位旅人把「{_title}」处理得挺漂亮，大家都念他的好",
                    sentiment="praise", origin="auto", game_day=game_day,
                    initial_knowers=_knowers, heat=55)
            elif _knowers and _sum < 0:
                rumor_manager.generate(
                    "player", f"那位旅人处理「{_title}」不太地道，有人有意见",
                    sentiment="smear", origin="auto", game_day=game_day,
                    initial_knowers=_knowers, heat=55)
        except Exception as e:
            log.debug("[rumor] 危机生成失败: %s", e)
        try:
            await _broadcast_election_state(ws, game_day)
        except Exception as e:
            log.debug("[crisis] 刷新选举失败: %s", e)


async def _run_opponent_daily(term: dict, game_day: int, ws: WebSocket, force: bool = False) -> None:
    """对手 NPC 一批动作（异步）：纲领懒生 + 多个动作（visit/promise/smear）。

    动作数随任期推进 + 落后幅度增加；每个动作推送 opponent_action 消息给客户端。
    force=True 跳过"每日一批"守卫（用于定时多批 / 调试键即时触发）。
    """
    try:
        await opponent_ai.ensure_platform(term)
        # 传入当前比分，供追赶系数判断落后幅度
        view = election_store.get_current_term_view(game_day)
        scores = view.get("scores", {})
        player_score = float(scores.get("player", 0.0))
        opponent_score = float(scores.get(term["opponent_id"], 0.0))
        actions = await opponent_ai.run_daily_actions(
            term, game_day, player_score=player_score, opponent_score=opponent_score,
            force=force,
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


_DAY_THEME_TEXT = {
    "rally":    {"title": "📣 民意集会日", "hint": "镇民今天最爱串门。多拜访、送礼拉好感，为竞选起势。"},
    "debate":   {"title": "🎤 广场辩论日", "hint": "今天在广场举行镇长辩论，参加并亮明立场，赢取选民认同。"},
    "crisis":   {"title": "⚡ 突发危机日", "hint": "镇上出了乱子！妥善调解危机能大幅左右选情。"},
    "vote":     {"title": "🗳 投票日", "hint": "今天镇民投票，结果即将揭晓，做最后冲刺！"},
    "campaign": {"title": "🌿 竞选日", "hint": "继续拜访镇民、兑现承诺，稳住声望。"},
}


async def _handle_time_tick(msg: dict, ws: WebSocket) -> None:
    """接收客户端游戏时间 tick，每日 22:00 触发反思 + 9-18 激活意图。

    消息格式：{"type": "time_tick", "game_day": N, "game_hour": H}
    """
    sess = session_ctx.get()
    game_day = int(msg.get("game_day", -1))
    game_hour = int(msg.get("game_hour", 0))

    if game_day < 0 or sess is None:
        return

    sess.last_known_game_day = game_day

    # 新的一天首个醒来 tick → 推送当日主题节点事件（引导玩家 + 氛围）
    if game_day > sess.last_day_event_day and game_hour >= 7:
        sess.last_day_event_day = game_day
        try:
            term = election_store.ensure_term_active(game_day)
            di = election_store.day_index_in_term(term, game_day)
            theme = election_store.day_theme(di)
            await ws.send_text(json.dumps({
                "type": "day_event",
                "ok": True,
                "day_index": di,
                "term_days": TERM_DAYS,
                "theme": theme,
                **_DAY_THEME_TEXT.get(theme, _DAY_THEME_TEXT["campaign"]),
            }, ensure_ascii=False))
            log.info("[day_event] day=%d di=%d theme=%s", game_day, di, theme)
        except Exception as e:
            log.warning("[day_event] 推送失败: %s", e)

    # 激活当日待执行意图（每小时检查一次）
    await _activate_pending_intents(game_day, game_hour, ws)

    # 22:00+ 且是新的一天 → 触发反思（非阻塞）
    if game_hour >= 22 and game_day > sess.last_reflect_day:
        sess.last_reflect_day = game_day
        asyncio.create_task(manager.run_all_daily_reflections(game_day, intent_store))
        # 八卦每日降温：冷透的话题自动淡出
        try:
            faded = rumor_store.decay_daily()
            if faded:
                log.info("[rumor] 每日降温：%d 条话题淡出", faded)
        except Exception as e:
            log.debug("[rumor] decay 失败: %s", e)

    # 对手 NPC 动作：每 3 游戏小时一批（醒着时段 7-21、仅 campaign 阶段）
    if 7 <= game_hour <= 21:
        slot = game_day * 100 + (game_hour // 3)
        if slot > sess.last_opponent_action_slot:
            try:
                term = election_store.ensure_term_active(game_day)
                day_idx = election_store.day_index_in_term(term, game_day)
                phase = election_store.phase_of(day_idx)
                if phase == "campaign":
                    sess.last_opponent_action_slot = slot
                    asyncio.create_task(_run_opponent_daily(term, game_day, ws, force=True))
            except Exception as e:
                log.warning("[opponent_ai] 触发失败: %s", e)

    # 危机调解：每游戏日尝试抽一次（醒着时段，避免同日重复），概率触发
    # 避让选举关键日（辩论日/投票日），不打断选举演出
    if 8 <= game_hour <= 20 and game_day > sess.last_crisis_spawn_day:
        sess.last_crisis_spawn_day = game_day
        try:
            import random as _rnd
            skip_phase = False
            prob = 0.75
            try:
                term = election_store.ensure_term_active(game_day)
                di = election_store.day_index_in_term(term, game_day)
                skip_phase = election_store.phase_of(di) in ("debate", "vote")
                if election_store.day_theme(di) == "crisis":
                    prob = 1.0  # D3 突发危机日：保证触发一次
            except Exception:
                pass
            if not skip_phase and _rnd.random() < prob:
                view = crisis_manager.maybe_spawn(game_day, game_hour)
                if view is not None:
                    await _push_crisis_state(ws, view)
                    log.info("[crisis] day=%d h=%d 触发危机 %s", game_day, game_hour, view.get("template_id"))
        except Exception as e:
            log.warning("[crisis] 抽取失败: %s", e)

    # 危机软性截止：超时未处理 → 自动按「不管」结算 + 广播
    try:
        expired = await crisis_manager.check_expired(game_day, game_hour)
        if expired and expired.get("ok"):
            await ws.send_text(json.dumps({"type": "crisis_result", **expired}, ensure_ascii=False))
            for a in expired.get("affected", []):
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
            await _push_crisis_state(ws, None)
            await _broadcast_election_state(ws, game_day)
    except Exception as e:
        log.warning("[crisis] 截止检查失败: %s", e)

    # 镇长政务任务：现任镇长期间按节流（醒着/每日额度/冷却）自动刷新
    try:
        if _player_is_mayor():
            v = mayor_task_manager.maybe_spawn(game_day, game_hour)
            if v is not None:
                await _push_mayor_task_state(ws)
                log.info("[mayor_task] day=%d h=%d 刷新任务 %s",
                         game_day, game_hour, v.get("task_type"))
    except Exception as e:
        log.warning("[mayor_task] 刷新失败: %s", e)

    # 选举权重重算：每日 22:00+ 重算一次（与反思同时机），并推送状态
    if game_hour >= 22 and game_day > sess.last_election_recompute_day:
        sess.last_election_recompute_day = game_day
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
    needed_day = int(term["start_day"]) + (VOTE_DAY_INDEX - 1)  # day_index=VOTE_DAY_INDEX
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
