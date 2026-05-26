"""Animal Agent：智能记忆 + 反思 + 世界事件感知。"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Dict, Any, List, Optional

from llm import LLMClient
from memory import MemoryStore, Memory
from profile import PlayerProfile
from world_events import WorldEventStore, WorldEvent
from affection import AffectionStore, level_of, level_label, delta_for_chat
from retrieval import retrieve_relevant
from fact_extractor import extract_facts, estimate_importance
from reflection import reflect_if_needed
import items as items_module
from gifts import GiftStore, compute_delta, pref_label


log = logging.getLogger("agent")


SYSTEM_PROMPT_TEMPLATE = """你是 {name}，一只 {species}，职业是 {occupation}。

【性格】{personality}
【说话风格】{speech_style}
【口头禅】{catchphrase}

你住在「怪物森林」——一个住满了奇形怪状但和善的怪物居民的奇幻森林。
{world_facts_block}

【当前情境】
- 游戏时间：{game_time}
- 你正在：{location}
- 当前在做：{intent}

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
8. 你对玩家的好感度（见上文）应自然影响语气：好感越高越亲近热络，好感为负则冷淡甚至排斥。"""


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
        max_history_turns: int = 12,
    ) -> None:
        self.persona = persona
        self.llm = llm
        self.memory = memory
        self.profile = profile
        self.world = world
        self.affection = affection
        self.gifts = gifts
        self.max_history_turns = max_history_turns

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

    def _build_reflections_block(self) -> str:
        refl = self.memory.reflections(self.animal_id, n=3)
        if not refl:
            return ""
        lines = "\n".join(f"- {r.content}" for r in refl)
        return "\n【你的近期反思】\n" + lines

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
        return f"\n【你对玩家的好感度】{label}（{v}/100）\n- {hint}"

    def _build_system_prompt(self, context: Dict[str, Any], query: str) -> str:
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
            world_facts_block=self._build_world_facts_block(),
            player_profile_block=self._build_player_profile_block(),
            affection_block=self._build_affection_block(),
            relevant_memories_block=self._build_relevant_memories_block(query),
            reflections_block=self._build_reflections_block(),
            world_events_block=self._build_world_events_block(),
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

        # 自己开口也存为 dialog 记忆
        self.memory.add(
            self.animal_id, reply,
            type="dialog", speaker="self",
            importance=3,
            game_time=context.get("time", ""),
        )
        # 好感度：每个 NPC 每"游戏日"最多 +1（首次/久别重逢），同一日不再加
        game_day = int(context.get("game_day", -1))
        aff = self.affection.adjust_for_greet(self.animal_id, game_day)
        return {"text": reply, "affection": aff}

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
            importance=2,
            game_time=context.get("time", ""),
        )
        return line

    async def reply(self, user_text: str, context: Dict[str, Any]) -> Dict[str, Any]:        # 1. 构造 prompt（含检索到的相关记忆等）
        sys_prompt = self._build_system_prompt(context, query=user_text)
        history = self._build_recent_history()

        messages: List[Dict[str, str]] = [{"role": "system", "content": sys_prompt}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_text})

        reply_text = await self.llm.chat(messages, max_tokens=200, temperature=0.95)

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
            importance=3,
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

        return {"text": reply_text, "affection": aff}

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
            importance=6 if abs(delta) >= 8 else 4,
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

        return {
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
    ) -> None:
        max_turns = int(os.getenv("MAX_HISTORY_TURNS", "12"))
        self._agents: Dict[str, Agent] = {
            aid: Agent(p, llm, memory, profile, world, affection, gifts, max_history_turns=max_turns)
            for aid, p in personas.items()
        }

    def get(self, animal_id: str) -> Optional[Agent]:
        return self._agents.get(animal_id)

    def all_ids(self) -> List[str]:
        return list(self._agents.keys())

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
            importance=2,
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
                importance=2,
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
