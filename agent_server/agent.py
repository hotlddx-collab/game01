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
from retrieval import retrieve_relevant
from fact_extractor import extract_facts, estimate_importance
from reflection import reflect_if_needed


log = logging.getLogger("agent")


SYSTEM_PROMPT_TEMPLATE = """你是 {name}，一只 {species}，职业是 {occupation}。

【性格】{personality}
【说话风格】{speech_style}
【口头禅】{catchphrase}

你住在动物森林小镇，邻居有其他拟人动物。
{world_facts_block}

【当前情境】
- 游戏时间：{game_time}
- 你正在：{location}
- 当前在做：{intent}

{player_profile_block}
{relevant_memories_block}
{reflections_block}
{world_events_block}

【对话规则】
1. 用中文回复，每次只回 1-2 句，自然口语，符合上述性格和说话风格。
2. 不要说"作为 AI"或类似元话术，你就是这只动物本身。
3. 如果你已经知道玩家的名字（见上文），直接喊名字，不要再喊"旅人"。
4. 自然带入当前在做的事或所处地点，让对话有生活感。
5. 如果你听说过最近发生的事（世界事件），合适时可主动提起，让玩家觉得世界真实。
6. 不要复读相关记忆里的内容，要"利用"它们在对话中体现你认得人/记得事。
7. 别说太长，留白让玩家继续聊。"""


class Agent:
    def __init__(
        self,
        persona: Dict[str, Any],
        llm: LLMClient,
        memory: MemoryStore,
        profile: PlayerProfile,
        world: WorldEventStore,
        max_history_turns: int = 12,
    ) -> None:
        self.persona = persona
        self.llm = llm
        self.memory = memory
        self.profile = profile
        self.world = world
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

    async def greet(self, context: Dict[str, Any]) -> str:
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
        return reply

    async def reply(self, user_text: str, context: Dict[str, Any]) -> str:
        # 1. 构造 prompt（含检索到的相关记忆等）
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

        return reply_text

    async def _maybe_reflect(self) -> None:
        try:
            await reflect_if_needed(self.animal_id, self.name, self.memory, self.llm)
        except Exception as e:
            log.warning("reflect 异常: %s", e)

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
    ) -> None:
        max_turns = int(os.getenv("MAX_HISTORY_TURNS", "12"))
        self._agents: Dict[str, Agent] = {
            aid: Agent(p, llm, memory, profile, world, max_history_turns=max_turns)
            for aid, p in personas.items()
        }

    def get(self, animal_id: str) -> Optional[Agent]:
        return self._agents.get(animal_id)

    def all_ids(self) -> List[str]:
        return list(self._agents.keys())
