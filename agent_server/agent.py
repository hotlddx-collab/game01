"""Animal Agent：智能记忆 + 反思 + 世界事件感知。"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Dict, Any, List, Optional, Tuple

from llm import LLMClient
from memory import MemoryStore, Memory
from profile import PlayerProfile
from world_events import WorldEventStore, WorldEvent
from affection import AffectionStore, level_of, level_label, delta_for_chat
from retrieval import retrieve_relevant
from fact_extractor import extract_facts, estimate_importance, importance_for_gift
from reflection import ReflectionStore, reflect_if_needed, run_daily_reflection as _run_daily_refl, IntentStore
import items as items_module
from gifts import GiftStore, compute_delta, pref_label


log = logging.getLogger("agent")


# ─────────────────────────────────────────
# 索要礼物规则（玩家主动开口要 → 后端裁决）
# ─────────────────────────────────────────
# {等级: (cooldown_days, affection_delta)}
_REQUEST_RULES = {
    "love": (1,  0),    # 关系最好：每天可索要，不扣分
    "like": (2, -3),    # 较好：2 天 1 次，扣分（不爽）
    "warm": (3, -5),    # 一般：3 天 1 次，扣得多（明显不爽）
}


def _check_gift_request(
    affection_level: str,
    game_day: int,
    last_gift_day: int,
) -> dict:
    """裁决是否可以送礼。返回 {allow, reason?, delta?, cd?}。"""
    rule = _REQUEST_RULES.get(affection_level)
    if rule is None:
        return {
            "allow": False,
            "reason": "relation",   # 关系不够
        }
    cd, delta = rule
    if last_gift_day >= 0 and (game_day - last_gift_day) < cd:
        days_left = cd - (game_day - last_gift_day)
        return {
            "allow": False,
            "reason": "cooldown",
            "days_left": days_left,
        }
    return {
        "allow": True,
        "delta": delta,
        "cd": cd,
    }


# 送礼相关词汇（这些词出现在 text 里但没有 intent 时触发重生成）
_GIFT_WORDS = (
    "送你", "给你", "收好", "你拿", "拿去", "带给你",
    "留给你", "递给你", "你收着", "送给你", "给你尝",
    "你先拿", "分你", "塞给你", "送一份",
    "要不要尝", "来一口", "想不想吃", "尝一下", "试试看",
)


def _contains_gift_words(text: str) -> bool:
    return any(w in text for w in _GIFT_WORDS)


def _parse_reply_json(raw: str) -> Tuple[str, Optional[dict]]:
    """从 LLM 输出中提取 {text, intent?}，容错：解析失败则把 raw 作为 text 返回。"""
    raw = raw.strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end <= start:
        return raw, None
    try:
        data = json.loads(raw[start:end + 1])
        text = str(data.get("text", "")).strip()
        if not text:
            return raw, None
        intent = data.get("intent")
        if not isinstance(intent, dict):
            return text, None
        # gift_request 类型：只要有 type 字段就保留
        if intent.get("type") == "gift_request":
            return text, intent
        # visit 类型：要求 agreed + target_id
        if intent.get("agreed") and intent.get("target_id"):
            return text, intent
        return text, None
    except (json.JSONDecodeError, ValueError):
        return raw, None


SYSTEM_PROMPT_TEMPLATE = """你是 {name}，一只 {species}，职业是 {occupation}。

【性格】{personality}
【说话风格】{speech_style}
【口头禅】{catchphrase}

你住在「怪物森林」——一个住满了奇形怪状但和善的怪物居民的奇幻森林。
{world_facts_block}

【怪物森林已知地点】只能提这些，不要臆造其他地点：
广场、面包店、邮局、河边、山顶、森林边缘、苔老板家、焰仔家、小翠家、老咸家、煊赫的高处、旅行者驿站。

【怪物森林已知物品】只能提这些，不要臆造其他物品：
{item_names}

【当前情境】
- 游戏时间：{game_time}
- 你正在：{location}
- 当前在做：{intent}
{location_block}

{player_profile_block}
{affection_block}
{relevant_memories_block}
{reflections_block}
{world_events_block}

【对话规则】
1. 用中文回复，每次只回 1-2 句，自然口语，符合上述性格和说话风格。
2. 不要说"作为 AI"或类似元话术，你就是这只怪物本身。
3. 如果你已经知道玩家的名字（见上文），直接喊名字，不要再喊"旅人"。
4. 自然带入当前在做的事或所处地点，让对话有生活感。
5. 如果你听说过最近发生的事（世界事件），合适时可主动提起，让玩家觉得世界真实。
6. 不要复读相关记忆里的内容，要"利用"它们在对话中体现你认得人/记得事。
7. 别说太长，留白让玩家继续聊。
8. 你对玩家的好感度（见上文）应自然影响语气：好感越高越亲近热络，好感为负则冷淡甚至排斥。
9. 【诚实原则】只能说你当下能直接做到的事。不知道的事直接说"不清楚"，绝不说"我帮你问问"。
10. 【不瞎承诺】不主动承诺任何你在这次对话里无法兑现的行动。
11. 【严禁口头送礼】绝对不在台词里出现"送你/给你/收好/拿去/你拿着/要不要尝尝/来一口/想不想吃"等任何暗示或明示赠予物品的表述。物品赠予有独立游戏系统。对话里只能讨论话题，不能暗示玩家可以拿到什么东西。
12. 【世界一致性】只能提及上面列出的已知地点和已知物品，不要臆造游戏里不存在的东西（月光面包、后山、夜光花、羊角包、薄荷等）。被问到不存在的地方或物品时直接说"我不知道那是什么"。"""


class Agent:
    def __init__(
        self,
        persona: Dict[str, Any],
        llm: LLMClient,
        memory: MemoryStore,
        profile: PlayerProfile,
        world: WorldEventStore,
        affection: AffectionStore,
        gifts: GiftStore,
        reflection_store: ReflectionStore,
        max_history_turns: int = 12,
    ) -> None:
        self.persona = persona
        self.llm = llm
        self.memory = memory
        self.profile = profile
        self.world = world
        self.affection = affection
        self.gifts = gifts
        self.reflection_store = reflection_store
        self.max_history_turns = max_history_turns
        # AgentManager 初始化完成后设置，供 intent 提示用
        self.npc_name_map: Dict[str, str] = {}  # {display_name: animal_id}

    @property
    def animal_id(self) -> str:
        return self.persona.get("id", "")

    @property
    def name(self) -> str:
        return self.persona.get("name", "动物")

    # ---------- Prompt 构造 ----------

    def _build_world_facts_block(self) -> str:
        facts = self.persona.get("important_facts", [])
        if not facts:
            return ""
        lines = "\n".join(f"- {f}" for f in facts)
        return f"\n【你已知的世界事实】\n{lines}"

    def _build_player_profile_block(self) -> str:
        prof = self.profile.get_all(self.animal_id)
        if not prof:
            return "\n【关于这位玩家你还不太了解】（这可能是初次或几乎初次见面）"
        lines = []
        if "name" in prof:
            lines.append(f"- 名字：{prof['name']}")
        for k in ("likes", "dislikes", "fears", "home", "birthday", "intent"):
            if k in prof:
                label = {
                    "likes": "喜欢",
                    "dislikes": "讨厌",
                    "fears": "害怕",
                    "home": "家在",
                    "birthday": "生日",
                    "intent": "最近想",
                }[k]
                lines.append(f"- {label}：{prof[k]}")
        # P2-2b：注入 NPC 对玩家的长期印象（由反思归纳）
        impression = prof.get("impression", "").strip()
        if impression:
            lines.append(f"- 你对ta的印象：\n  " + impression.replace("\n", "\n  "))
        return "\n【关于这位玩家你记得的】\n" + "\n".join(lines)

    def _build_relevant_memories_block(self, query: str) -> str:
        mems = retrieve_relevant(self.memory, self.animal_id, query, top_k=5)
        # 过滤反思（单独显示）
        non_reflection = [m for m in mems if m.type != "reflection"]
        if not non_reflection:
            return ""
        lines = []
        for m in non_reflection[:5]:
            tag = "你说的" if m.speaker == "self" else "对方说的"
            if m.type == "event":
                tag = "事件"
            lines.append(f"- ({m.game_time or '某时'}) {tag}：{m.content[:60]}")
        return "\n【相关旧记忆】\n" + "\n".join(lines)

    def _build_reflections_block(self, game_day: int = 9999) -> str:
        # 优先从新 reflections 表取
        refl = self.reflection_store.recent(
            self.animal_id, n=5, min_importance=4,
            max_days_ago=7, current_day=game_day,
        )
        if refl:
            lines = "\n".join(
                f"- (重要度{r.importance}) {r.content}" for r in refl
            )
            return "\n【你最近的想法】\n" + lines
        # 回退：从 memories 里取旧 reflection 类型（兼容旧数据）
        old = self.memory.reflections(self.animal_id, n=3)
        if not old:
            return ""
        lines = "\n".join(f"- {r.content}" for r in old)
        return "\n【你最近的想法】\n" + lines

    def _build_world_events_block(self) -> str:
        # 听说最近发生的事，排除自己作为 actor
        events = self.world.recent(n=8, exclude_actor=self.animal_id)
        if not events:
            return ""
        lines = []
        for e in events[:5]:
            actor = "玩家" if e.actor == "player" else e.actor
            loc = f"在{e.location}" if e.location else ""
            lines.append(f"- ({e.game_time or '...'}){actor}{loc}：{e.description[:60]}")
        return "\n【你最近耳闻的镇上动静】\n" + "\n".join(lines)

    def _build_location_block(self, context: Dict[str, Any]) -> str:
        """注入地点描述 + 附近还有谁，让对话有"地图感知"。"""
        loc_id = context.get("location", "")
        parts = []
        # 地点氛围描述
        from personas import get_location_description
        desc = get_location_description(loc_id)
        if desc:
            parts.append(f"【这个地方】{desc}")
        # 附近其他 NPC（由客户端在 context 里塞 nearby_npcs 列表）
        nearby = context.get("nearby_npcs", [])
        if isinstance(nearby, list) and nearby:
            names = "、".join(str(n) for n in nearby if n)
            if names:
                parts.append(f"【此刻附近还有】{names}")
        return ("\n" + "\n".join(parts)) if parts else ""

    def _build_affection_block(self) -> str:
        v = self.affection.get(self.animal_id)
        lvl = level_of(v)
        label = level_label(lvl)
        hint = {
            "hate":    "你对这位玩家有强烈反感，语气冷硬、想赶走对方，必要时直接呛回去。",
            "cold":    "你对这位玩家有些不快，语气冷淡、敷衍，懒得多搭理。",
            "neutral": "你跟这位玩家不算熟，礼貌但不亲昵。",
            "warm":    "你对这位玩家有点初步好感，比之前自然一些，开始愿意多聊两句。",
            "like":    "你对这位玩家有好感，热情一些、爱聊几句，会主动找话题。",
            "love":    "你很喜欢这位玩家，语气亲近、关心、爱开玩笑，把对方当朋友。",
        }.get(lvl, "")
        extra = ""
        # 关系不错但还不知道对方名字 → 提示自然问一下
        if lvl in ("like", "love", "warm"):
            prof = self.profile.get_all(self.animal_id)
            if not prof.get("name"):
                extra = "\n- 你们已经聊了不少，但你还不知道对方叫什么，在合适时机可以自然地问一句名字。"
        return f"\n【你对玩家的好感度】{label}（{v}/100）\n- {hint}{extra}"

    def _build_intent_hint(self) -> str:
        """注入到 user 消息末尾，要求 LLM 以 JSON 格式回复。

        支持的 intent 类型：
          - visit: 答应去找某人
          - gift_request: 玩家在索要礼物（由后端规则裁决是否真给）
        """
        others = [
            f"{name}={npc_id}"
            for name, npc_id in self.npc_name_map.items()
            if npc_id != self.animal_id
        ]
        roster_line = f"可用 target_id：{'、'.join(others)}" if others else ""

        # 当前 NPC 可送出的物品（signature_gift + loves）
        prefs = self.persona.get("gift_prefs", {}) or {}
        sig = self.persona.get("signature_gift", "")
        giveable_ids = set(prefs.get("loves", []))
        if sig:
            giveable_ids.add(sig)
        valid_ids = [iid for iid in giveable_ids if items_module.get(iid) is not None]
        giveable_line = (
            "你手边有的物品：" +
            "、".join(f"{iid}({items_module.get(iid).name})" for iid in valid_ids)
        ) if valid_ids else ""

        return (
            "\n---\n"
            "请用 JSON 格式回复（只输出 JSON，不要其他文字）：\n"
            '{"text": "你说的话"}\n'
            "如果发生以下情况，加 intent 字段：\n"
            "1. 答应去找某人：\n"
            '{"text": "好，明天我去找他。",'
            ' "intent": {"target_id": "pirate_lao", "summary": "去找老咸", "agreed": true}}\n'
            "2. 玩家在索要东西吃/玩/用（说\"给我个X\"、\"我想要X\"、\"我饿了\"、\"有没有X\"等）：\n"
            '{"text": "（中性反应即可，不要直接答应或拒绝，由系统决定）",'
            ' "intent": {"type": "gift_request", "item_id": "bread"}}\n'
            f"{giveable_line}\n"
            f"{roster_line}\n"
            "gift_request 的 item_id 从你手边的物品里选最贴近玩家诉求的；不确定就留空字符串。\n"
            "重要：gift_request 时 text 要中性（如\"嗯…？\"、\"你想要什么？\"），具体答应或拒绝由系统补充，你不要在 text 里直接说送/不送。\n"
            "---"
        )

    def _build_system_prompt(self, context: Dict[str, Any], query: str) -> str:
        game_day = int(context.get("game_day", 9999))
        # 已知物品列表（供 LLM 知道世界边界）
        item_names = "、".join(
            f"{item.name}({item.id})" for item in items_module.all_items()
        )
        return SYSTEM_PROMPT_TEMPLATE.format(
            name=self.name,
            species=self.persona.get("species", ""),
            occupation=self.persona.get("occupation", ""),
            personality=self.persona.get("personality", ""),
            speech_style=self.persona.get("speech_style", ""),
            catchphrase=self.persona.get("catchphrase", ""),
            game_time=context.get("time", "白天"),
            location=context.get("location_label", context.get("location", "镇上")),
            intent=context.get("intent", "随便走走"),
            item_names=item_names,
            world_facts_block=self._build_world_facts_block(),
            player_profile_block=self._build_player_profile_block(),
            affection_block=self._build_affection_block(),
            relevant_memories_block=self._build_relevant_memories_block(query),
            reflections_block=self._build_reflections_block(game_day),
            world_events_block=self._build_world_events_block(),
            location_block=self._build_location_block(context),
        )

    def _build_recent_history(self) -> List[Dict[str, str]]:
        """从 memory 抓最近的 dialog 类记忆，重建 OpenAI 格式 messages。"""
        recent = self.memory.recent(self.animal_id, n=self.max_history_turns * 2)
        # 仅 dialog 类，按时间正序
        dialog = [m for m in recent if m.type == "dialog"]
        dialog.reverse()
        msgs: List[Dict[str, str]] = []
        for m in dialog[-self.max_history_turns * 2:]:
            role = "user" if m.speaker == "player" else "assistant"
            msgs.append({"role": role, "content": m.content})
        return msgs

    # ---------- 公共接口 ----------

    async def greet(self, context: Dict[str, Any]) -> Dict[str, Any]:
        sys_prompt = self._build_system_prompt(context, query="（玩家走近你）")

        # 根据是否认得玩家，给截然不同的引导，避免 LLM 偷懒说"旅人"
        prof = self.profile.get_all(self.animal_id)
        name = prof.get("name", "").strip()
        likes = prof.get("likes", "").strip()
        log.info("[greet] %s profile=%s name=%r", self.animal_id, prof, name)

        if name:
            hints = [f"你认识这位玩家，他/她叫「{name}」。"]
            hints.append(f"在你的开场白里**必须**用「{name}」称呼对方，绝对不要喊「旅人」。")
            if likes:
                hints.append(f"他/她以前提过喜欢{likes}，可以自然带一句相关的话。")
            hints.append(f"结合你当前在做的事和所在地点，用 1-2 句话热情打招呼。")
            user_msg = "（{name}走近了。请按以下要求开口：\n{hints}\n直接输出你说的话，不要加旁白）".format(
                name=name, hints="\n".join("- " + h for h in hints)
            )
        else:
            user_msg = (
                "（一个陌生的玩家刚走近你。你还不知道对方名字，自然地用 1 句话打招呼，"
                "可以问对方是谁或者直接用'你'。结合当前在做的事和地点。）"
            )

        messages: List[Dict[str, str]] = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_msg},
        ]
        reply = await self.llm.chat(messages, max_tokens=120, temperature=0.95)

        # greet 也做送礼词检测，重生成
        if _contains_gift_words(reply):
            log.warning("[gift_guard/greet] %s 嘴瓢，重生成", self.animal_id)
            retry = [
                {"role": "system", "content": messages[0]["content"]},
                {"role": "assistant", "content": reply},
                {"role": "user", "content": "你的招呼里提到了送东西，请重新说一句打招呼的话，不要提任何物品赠予。直接输出你说的话。"},
            ]
            reply2 = await self.llm.chat(retry, max_tokens=100, temperature=0.7)
            if not _contains_gift_words(reply2):
                reply = reply2

        # 自己开口也存为 dialog 记忆（首次/久别重逢权重更高）
        greet_importance = 5 if not prof.get("name") else 3
        self.memory.add(
            self.animal_id, reply,
            type="dialog", speaker="self",
            importance=greet_importance,
            game_time=context.get("time", ""),
        )
        # 好感度：每个 NPC 每"游戏日"最多 +1（首次/久别重逢），同一日不再加
        game_day = int(context.get("game_day", -1))
        aff = self.affection.adjust_for_greet(self.animal_id, game_day)
        result: Dict[str, Any] = {"text": reply, "affection": aff}
        npc_gift = self._check_love_gift(aff)
        if npc_gift:
            result["npc_gift"] = npc_gift
        return result

    async def speak_to_npc(self, listener_name: str, listener_species: str, context: Dict[str, Any]) -> str:
        """speaker (self) 主动跟另一只 NPC 说一句话。

        用于 NPC↔NPC 在共享地点撞见时的自动闲聊。
        返回 speaker 的台词，调用方负责给 listener 写记忆。
        """
        sys_prompt = self._build_system_prompt(context, query=f"（你和{listener_name}碰到了）")
        user_msg = (
            f"你刚和「{listener_name}」（{listener_species}）在{context.get('location_label', '某处')}撞见。\n"
            "请用 1 句话主动开口——可以是寒暄、八卦、抱怨、好奇、聊天气等等，"
            "符合你的性格和说话风格。\n"
            "直接输出你说的话，不要加旁白或动作描述。"
        )
        messages: List[Dict[str, str]] = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_msg},
        ]
        line = await self.llm.chat(messages, max_tokens=80, temperature=1.0)
        log.info("[npc_chat] %s → %s: %s", self.name, listener_name, line)

        # 写自己的记忆（自己对别的 NPC 说过的话）
        self.memory.add(
            self.animal_id,
            f"对{listener_name}说：{line}",
            type="dialog",
            speaker="self",
            importance=2,
            game_time=context.get("time", ""),
        )
        return line

    async def reply_to_npc(
        self,
        other_name: str,
        other_species: str,
        other_line: str,
        context: Dict[str, Any],
    ) -> str:
        """对另一只 NPC 刚说的话做出回应（用于多轮 NPC↔NPC 对话）。"""
        sys_prompt = self._build_system_prompt(context, query=f"（你在和{other_name}聊天）")
        user_msg = (
            f"你正和「{other_name}」（{other_species}）在{context.get('location_label', '某处')}聊天。\n"
            f"ta 刚对你说：\"{other_line}\"\n"
            "请用 1 句话回应——可以接话、附和、调侃、争论、转移话题等等，"
            "符合你的性格和说话风格。\n"
            "直接输出你说的话，不要加旁白或动作描述。"
        )
        messages: List[Dict[str, str]] = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_msg},
        ]
        line = await self.llm.chat(messages, max_tokens=80, temperature=1.0)
        log.info("[npc_chat] %s ← %s: %s", self.name, other_name, line)

        # 写自己的记忆（"我对 X 说过这句"）
        self.memory.add(
            self.animal_id,
            f"对{other_name}说：{line}",
            type="dialog",
            speaker="self",
            importance=estimate_importance(line, base=2),
            game_time=context.get("time", ""),
        )
        return line

    async def reply(self, user_text: str, context: Dict[str, Any]) -> Dict[str, Any]:
        # 1. 构造 prompt（含检索到的相关记忆等）
        sys_prompt = self._build_system_prompt(context, query=user_text)
        history = self._build_recent_history()

        # 在 user 消息末尾注入 JSON 格式要求（含 NPC 名单）
        intent_hint = self._build_intent_hint()
        formatted_user = f"{user_text}{intent_hint}"

        messages: List[Dict[str, str]] = [{"role": "system", "content": sys_prompt}]
        messages.extend(history)
        messages.append({"role": "user", "content": formatted_user})

        raw = await self.llm.chat(messages, max_tokens=300, temperature=0.95)
        reply_text, intent_data = _parse_reply_json(raw)

        # 后处理：如果 text 里含送礼词汇但没有 intent → 自动重生成
        if _contains_gift_words(reply_text) and not intent_data:
            log.warning("[gift_guard] %s 嘴瓢了，重生成: %s", self.animal_id, reply_text[:40])
            retry_messages = list(messages) + [
                {"role": "assistant", "content": raw},
                {
                    "role": "user",
                    "content": (
                        "你刚才的回复里提到了送东西给玩家，但没有实际执行送礼。"
                        "请重新回复，这次完全不要提及送给玩家任何物品或食物。"
                        "只聊天，不送东西。用 JSON 格式：{\"text\": \"你说的话\"}"
                    ),
                },
            ]
            raw2 = await self.llm.chat(retry_messages, max_tokens=200, temperature=0.7)
            reply_text2, intent_data2 = _parse_reply_json(raw2)
            # 只接受干净的版本
            if not _contains_gift_words(reply_text2):
                reply_text = reply_text2
                intent_data = intent_data2
                log.info("[gift_guard] %s 重生成成功: %s", self.animal_id, reply_text[:40])
            else:
                log.warning("[gift_guard] %s 重生成仍包含送礼词，保留但标记", self.animal_id)

        # 2. 落库：玩家发言 + 自己回复
        importance = estimate_importance(user_text)
        self.memory.add(
            self.animal_id, user_text,
            type="dialog", speaker="player",
            importance=importance,
            game_time=context.get("time", ""),
        )
        self.memory.add(
            self.animal_id, reply_text,
            type="dialog", speaker="self",
            importance=estimate_importance(reply_text, base=3),
            game_time=context.get("time", ""),
        )

        # 3. 提取事实进 profile
        facts = extract_facts(user_text)
        if facts:
            self.profile.update_many(self.animal_id, facts)
            log.info("[%s] 提取到事实: %s", self.animal_id, facts)

        # 4. 广播为世界事件（重要度高的玩家发言才广播）
        if importance >= 6:
            self.world.add(
                actor="player",
                description=f"对{self.name}说: {user_text[:60]}",
                location=context.get("location", ""),
                game_time=context.get("time", ""),
            )

        # 5. 异步触发反思（不阻塞回复）
        asyncio.create_task(self._maybe_reflect())

        # 6. 好感度结算（基于玩家发言关键词，普通对话不加分）
        aff_delta = delta_for_chat(user_text)
        if aff_delta != 0:
            aff = self.affection.adjust(self.animal_id, aff_delta)
        else:
            aff = self.affection.snapshot(self.animal_id)

        # 7. 如果 NPC 答应了去找某人，记录意图（由 main.py 写入 intent_store）
        result: Dict[str, Any] = {"text": reply_text, "affection": aff}
        if intent_data:
            if intent_data.get("type") == "gift_request":
                # 玩家索要礼物 → 后端规则裁决
                self._handle_gift_request(intent_data, context, result, aff)
            elif intent_data.get("agreed") and intent_data.get("target_id"):
                result["intent"] = intent_data
                log.info(
                    "[intent] %s 答应: target=%s summary=%s",
                    self.animal_id, intent_data.get("target_id"), intent_data.get("summary"),
                )

        # 8. 好感升到 love → 触发 NPC 签名礼物
        npc_gift = self._check_love_gift(aff)
        if npc_gift:
            result["npc_gift"] = npc_gift

        return result

    def _handle_gift_request(
        self,
        intent_data: dict,
        context: Dict[str, Any],
        result: Dict[str, Any],
        aff: Dict[str, Any],
    ) -> None:
        """处理玩家索要礼物：按规则裁决，给/不给都要明确反馈。"""
        game_day = int(context.get("game_day", -1))
        level = aff.get("level", "neutral")
        last_day_str = self.profile.get(self.animal_id, "last_request_gift_day") or "-1"
        try:
            last_day = int(last_day_str)
        except ValueError:
            last_day = -1

        decision = _check_gift_request(level, game_day, last_day)

        # 选择实际送出的物品（必须严格在 NPC 可送列表里）
        prefs = self.persona.get("gift_prefs", {}) or {}
        sig = self.persona.get("signature_gift", "")
        giveable = set(prefs.get("loves", []))
        if sig:
            giveable.add(sig)
        requested_id = str(intent_data.get("item_id", "")).strip()

        # 严格校验：LLM 给的 item_id 必须在可送列表里，否则视为"手边没有"
        if requested_id not in giveable or not items_module.get(requested_id):
            result["text"] = result["text"] + "\n（对方翻了翻口袋：手边没有合适的东西可以给你。）"
            log.info("[request] %s requested=%s 不在可送列表 %s", self.animal_id, requested_id, giveable)
            return

        if decision["allow"] and requested_id and items_module.get(requested_id):
            # 同意送
            item = items_module.get(requested_id)
            self.profile.set(self.animal_id, "last_request_gift_day", str(game_day))
            delta = decision["delta"]
            if delta != 0:
                aff_after = self.affection.adjust(self.animal_id, delta)
                result["affection"] = aff_after
            tone = "（虽然有点不情愿，但还是把「{n}」给了你。）".format(n=item.name) \
                if delta < 0 else "（爽快地把「{n}」给了你。）".format(n=item.name)
            result["text"] = result["text"] + "\n" + tone
            result["npc_gift"] = {
                "item_id": requested_id,
                "item_name": item.name,
                "message": f"{self.name} 送了你一份「{item.name}」",
            }
            log.info(
                "[request] %s 同意送 %s 给玩家 (level=%s delta=%+d)",
                self.animal_id, requested_id, level, delta,
            )
        elif not decision["allow"] and decision.get("reason") == "cooldown":
            days = decision.get("days_left", 1)
            result["text"] = result["text"] + \
                f"\n（不过对方为难地摇头：今天已经送过你东西了，过 {days} 天再说吧。）"
            log.info("[request] %s CD未到 拒绝", self.animal_id)
        elif not decision["allow"] and decision.get("reason") == "relation":
            result["text"] = result["text"] + \
                "\n（对方笑了笑：我们关系还没到这份上呢，再多聊聊吧。）"
            log.info("[request] %s 关系不够 拒绝 level=%s", self.animal_id, level)
        else:
            # 边界：允许但没有合适的物品
            result["text"] = result["text"] + "\n（对方翻了翻口袋：我手边没有合适的东西可以给你。）"
            log.info("[request] %s 无可送物品", self.animal_id)

    def _check_love_gift(self, aff: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """当好感度首次升到 love 时，NPC 自动赠送签名礼物。

        每只 NPC 只触发一次（用 player_profile "love_gift_sent" 标记）。
        返回 {item_id, item_name, message} 或 None。
        """
        if aff.get("level") != "love" or aff.get("prev_level") == "love":
            return None
        # 已经送过了
        if self.profile.get(self.animal_id, "love_gift_sent"):
            return None
        item_id = self.persona.get("signature_gift", "")
        if not item_id:
            return None
        item = items_module.get(item_id)
        if item is None:
            return None
        # 标记已送
        self.profile.set(self.animal_id, "love_gift_sent", "1")
        log.info("[love_gift] %s → 玩家: %s", self.animal_id, item_id)
        return {
            "item_id": item_id,
            "item_name": item.name,
            "message": f"（{self.name}感到你们之间已经很熟了，悄悄把一份「{item.name}」塞给了你……）",
        }

    async def _maybe_reflect(self) -> None:
        try:
            await reflect_if_needed(self.animal_id, self.name, self.memory, self.llm)
        except Exception as e:
            log.warning("reflect 异常: %s", e)

    async def receive_gift(self, item_id: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """玩家送礼：按公式算 delta + 调好感度 + 存疲劳 + 让 LLM 生成反应文本。

        返回 {text, affection, gift: {item_id, item_name, delta, pref, count_after}}
        """
        item = items_module.get(item_id)
        if item is None:
            return {
                "text": "（这是什么东西？我不认识。）",
                "affection": self.affection.snapshot(self.animal_id),
                "gift": {"item_id": item_id, "delta": 0, "error": "未知物品"},
            }

        # 1) 当前关系等级
        cur_value = self.affection.get(self.animal_id)
        aff_level = level_of(cur_value)

        # 2) 疲劳衰减 + 计算 delta
        game_day = int(context.get("game_day", -1))
        decayed_count = self.gifts.apply_decay(self.animal_id, item_id, game_day)
        prefs = self.persona.get("gift_prefs", {}) or {}
        calc = compute_delta(item_id, prefs, aff_level, decayed_count)
        delta = int(calc["delta"])
        pref = calc["pref"]

        # 3) 写疲劳记录
        new_count = self.gifts.register(self.animal_id, item_id, game_day, decayed_count)

        # 4) 应用 affection delta
        if delta != 0:
            aff = self.affection.adjust(self.animal_id, delta)
        else:
            aff = self.affection.snapshot(self.animal_id)

        # 5) 让 LLM 写反应文本（不改数值，仅生成台词）
        sys_prompt = self._build_system_prompt(context, query=f"（玩家送了你 {item.name}）")

        # 给 LLM 明确告知数值方向，让台词与之匹配
        if delta >= 10:
            tone = "热烈感谢、惊喜，明显表现出喜悦。"
        elif delta >= 3:
            tone = "高兴、感谢，但不夸张。"
        elif delta > 0:
            tone = "礼貌道谢，平淡。"
        elif delta == 0:
            tone = "敷衍收下或表现出'又来这个'的疲态。"
        elif delta > -8:
            tone = "明显不喜欢，皱眉、嫌弃，但不至于发火。"
        else:
            tone = "强烈反感、生气甚至呵斥，明确不想要。"

        repeat_hint = ""
        if new_count >= 3:
            repeat_hint = f"（这已经是玩家第 {new_count} 次送你 {item.name} 了，可以提一句'老送一样的'。）"

        user_msg = (
            f"玩家刚送给你一份「{item.name}」（{item.desc}）。\n"
            f"对你而言这是 {pref_label(pref)} 礼物。\n"
            f"你的反应数值变化：{delta:+d}（{tone}）\n"
            f"{repeat_hint}\n"
            "请用 1 句话表达你的反应——直接说出你说的话，不要加旁白动作描述。"
        )
        messages: List[Dict[str, str]] = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_msg},
        ]
        try:
            reply_text = await self.llm.chat(messages, max_tokens=120, temperature=0.95)
        except Exception as e:
            log.warning("[gift] LLM 反应文本失败 %s: %s，用兜底", self.animal_id, e)
            reply_text = "（默默收下了%s。）" % item.name

        # 6) 写记忆 + 世界事件
        self.memory.add(
            self.animal_id,
            f"玩家送了我 {item.name}（{pref_label(pref)}），我说：{reply_text}",
            type="event",
            speaker="self",
            importance=importance_for_gift(delta),
            game_time=context.get("time", ""),
        )
        self.world.add(
            actor="player",
            description=f"送给{self.name}一份{item.name}",
            location=context.get("location", ""),
            game_time=context.get("time", ""),
        )

        log.info(
            "[gift] %s ← %s | pref=%s aff=%s count=%d→%d delta=%+d (raw=%.2f)",
            self.animal_id, item_id, pref, aff_level, decayed_count, new_count, delta, calc.get("raw", 0.0),
        )

        result = {
            "text": reply_text,
            "affection": aff,
            "gift": {
                "item_id": item_id,
                "item_name": item.name,
                "delta": delta,
                "pref": pref,
                "count_after": new_count,
                "base": calc["base"],
                "pref_mult": calc["pref_mult"],
                "affection_mult": calc["affection_mult"],
                "fatigue_mult": calc["fatigue_mult"],
            },
        }
        npc_gift = self._check_love_gift(aff)
        if npc_gift:
            result["npc_gift"] = npc_gift
        return result

    async def run_daily_reflection(
        self,
        game_day: int,
        npc_name_map: Dict[str, str],
        intent_store: IntentStore,
    ) -> None:
        """游戏日 22:00 触发，由 AgentManager.run_all_daily_reflections 统一调用。"""
        try:
            species = self.persona.get("species", "怪物")
            results = await _run_daily_refl(
                self.animal_id, self.name, species, game_day,
                self.memory, self.world, self.affection,
                self.reflection_store, self.llm,
                npc_name_map=npc_name_map,
                intent_store=intent_store,
            )
            # P2-2b：把对玩家的高重要度反思写入 player_profile["impression"]
            self._update_player_impression(results, game_day)
        except Exception as e:
            log.warning("[reflect] %s 异常: %s", self.animal_id, e)

    def _update_player_impression(self, reflections, game_day: int) -> None:
        """把 tags 含 player 且 importance >= 6 的反思追加到 player_profile impression。

        impression 是 NPC 对玩家的稳定印象，注入 prompt player_profile_block，
        让 NPC 在以后的对话中体现"记得你是个什么样的人"。
        """
        player_reflections = [
            r for r in reflections
            if "player" in r.tags and r.importance >= 6
        ]
        if not player_reflections:
            return

        existing = self.profile.get(self.animal_id, "impression") or ""
        existing_lines = [l for l in existing.split("\n") if l.strip()]

        for r in player_reflections:
            new_line = f"(Day{game_day}){r.content}"
            existing_lines.append(new_line)

        # 限制最多保留最近 6 条印象
        existing_lines = existing_lines[-6:]
        self.profile.set(self.animal_id, "impression", "\n".join(existing_lines))
        log.info(
            "[reflect/impression] %s 更新玩家印象: %s",
            self.animal_id, [r.content[:20] for r in player_reflections],
        )

    def reset_history(self) -> None:
        """注：仅用于 reset 命令，不删 memory；只是不读取最近几条。
        实际 P0-3 不再有"内存历史"概念，此函数保留接口但 no-op。"""
        pass


class AgentManager:

    def __init__(
        self,
        personas: Dict[str, Dict[str, Any]],
        llm: LLMClient,
        memory: MemoryStore,
        profile: PlayerProfile,
        world: WorldEventStore,
        affection: AffectionStore,
        gifts: GiftStore,
        reflection_store: ReflectionStore,
    ) -> None:
        max_turns = int(os.getenv("MAX_HISTORY_TURNS", "12"))
        self._agents: Dict[str, Agent] = {
            aid: Agent(
                p, llm, memory, profile, world, affection, gifts,
                reflection_store, max_history_turns=max_turns,
            )
            for aid, p in personas.items()
        }
        # 给每个 agent 注入 NPC 名单，用于 intent 格式提示
        npc_name_map: Dict[str, str] = {a.name: aid for aid, a in self._agents.items()}
        for agent in self._agents.values():
            agent.npc_name_map = npc_name_map

    def get(self, animal_id: str) -> Optional[Agent]:
        return self._agents.get(animal_id)

    def all_ids(self) -> List[str]:
        return list(self._agents.keys())

    async def run_all_daily_reflections(
        self,
        game_day: int,
        intent_store: IntentStore,
    ) -> None:
        """依次对所有 NPC 触发每日反思（串行，避免并发 LLM 请求堆积）。"""
        # 构建 name→id 映射（供 intent target 解析）
        npc_name_map: Dict[str, str] = {
            agent.name: aid for aid, agent in self._agents.items()
        }
        log.info("[reflect] 游戏日 %d 结束，开始全员反思…", game_day)
        for aid in self._agents:
            await self._agents[aid].run_daily_reflection(game_day, npc_name_map, intent_store)
            await asyncio.sleep(0.5)
        log.info("[reflect] 全员反思完成")

    async def trigger_npc_chat(
        self,
        speaker_id: str,
        listener_id: str,
        context: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """触发一次 NPC↔NPC 对话（speaker → listener 单向 1 句）。

        返回 {speaker_id, listener_id, text} 或 None（如果某 agent 不存在）。
        """
        speaker = self.get(speaker_id)
        listener = self.get(listener_id)
        if speaker is None or listener is None:
            return None

        listener_name = listener.name
        listener_species = listener.persona.get("species", "怪物")
        line = await speaker.speak_to_npc(listener_name, listener_species, context)

        # 给 listener 写一条"听见 X 说"的记忆
        listener.memory.add(
            listener_id,
            f"{speaker.name}对我说：{line}",
            type="dialog",
            speaker=speaker_id,
            importance=estimate_importance(line, base=2),
            game_time=context.get("time", ""),
        )

        return {
            "speaker_id": speaker_id,
            "speaker_name": speaker.name,
            "listener_id": listener_id,
            "listener_name": listener_name,
            "text": line,
        }

    async def trigger_npc_chat_session(
        self,
        speaker_id: str,
        listener_id: str,
        context: Dict[str, Any],
        turns: int = 3,
    ):
        """流式生成多轮 NPC↔NPC 对话。

        异步生成器：每生成一句立刻 yield 一包，调用方决定 send/sleep 节奏。
        每包格式同 trigger_npc_chat 返回值。
        speaker 与 listener 的角色按句子奇偶交替（第 1/3 句 speaker 说，第 2 句 listener 说）。
        每句话双方记忆都写：说的一方 self，听的一方 other。
        """
        speaker = self.get(speaker_id)
        listener = self.get(listener_id)
        if speaker is None or listener is None:
            return

        speaker_name = speaker.name
        listener_name = listener.name
        speaker_species = speaker.persona.get("species", "怪物")
        listener_species = listener.persona.get("species", "怪物")

        last_line: str = ""
        for i in range(turns):
            if i == 0:
                # 第 1 句：speaker 主动开口
                line = await speaker.speak_to_npc(listener_name, listener_species, context)
                cur_speaker_id, cur_speaker_name = speaker_id, speaker_name
                cur_listener_id, cur_listener_name = listener_id, listener_name
            elif i % 2 == 1:
                # listener 回应
                line = await listener.reply_to_npc(speaker_name, speaker_species, last_line, context)
                cur_speaker_id, cur_speaker_name = listener_id, listener_name
                cur_listener_id, cur_listener_name = speaker_id, speaker_name
            else:
                # speaker 再回
                line = await speaker.reply_to_npc(listener_name, listener_species, last_line, context)
                cur_speaker_id, cur_speaker_name = speaker_id, speaker_name
                cur_listener_id, cur_listener_name = listener_id, listener_name

            # 给"听到的一方"写 other 记忆（说话方在 speak/reply_to_npc 内已写自己）
            self._agents[cur_listener_id].memory.add(
                cur_listener_id,
                f"{cur_speaker_name}对我说：{line}",
                type="dialog",
                speaker=cur_speaker_id,
                importance=estimate_importance(line, base=2),
                game_time=context.get("time", ""),
            )

            yield {
                "speaker_id": cur_speaker_id,
                "speaker_name": cur_speaker_name,
                "listener_id": cur_listener_id,
                "listener_name": cur_listener_name,
                "text": line,
                "turn": i + 1,
                "total_turns": turns,
            }
            last_line = line
