"""反思机制（P2-1）+ 意图提取（P2-B）。

新版：每个游戏日 22:00 由 main.py time_tick 触发，LLM 将当日记忆压缩成
3-5 条带 importance + tags 的结构化反思，存入独立 reflections 表。
若反思含 tags=["intent"]，自动解析目标 NPC 并写入 animal_intents 表。
后续对话 prompt 注入"最近的想法"块，让 NPC 显得有持续思考。

旧版 reflect_if_needed() 保留（累积记忆数触发），兼容对话后被动调用。
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from db import get_conn
from memory import Memory, MemoryStore
from llm import LLMClient


log = logging.getLogger("reflection")


# ──────────────────────────────────────────────
# ReflectionStore
# ──────────────────────────────────────────────

@dataclass
class Reflection:
    id: int
    animal_id: str
    game_day: int
    content: str
    importance: int
    tags: List[str] = field(default_factory=list)
    created_at: int = 0


class ReflectionStore:
    """reflections 表的 CRUD 接口。"""

    def add(
        self,
        animal_id: str,
        game_day: int,
        content: str,
        importance: int = 5,
        tags: Optional[List[str]] = None,
    ) -> None:
        tags_str = ",".join(tags) if tags else ""
        with get_conn() as conn:
            conn.execute(
                """INSERT INTO reflections
                   (animal_id, game_day, content, importance, tags, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (animal_id, game_day, content, importance, tags_str, int(time.time())),
            )

    def recent(
        self,
        animal_id: str,
        n: int = 5,
        min_importance: int = 1,
        max_days_ago: int = 30,
        current_day: int = 9999,
    ) -> List[Reflection]:
        """按重要度降序取最近若干天的反思。"""
        min_day = max(0, current_day - max_days_ago)
        with get_conn() as conn:
            rows = conn.execute(
                """SELECT * FROM reflections
                   WHERE animal_id = ?
                     AND importance >= ?
                     AND game_day >= ?
                   ORDER BY game_day DESC, importance DESC
                   LIMIT ?""",
                (animal_id, min_importance, min_day, n),
            ).fetchall()
        return [_row_to_refl(r) for r in rows]

    def has_reflected_today(self, animal_id: str, game_day: int) -> bool:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM reflections WHERE animal_id = ? AND game_day = ? LIMIT 1",
                (animal_id, game_day),
            ).fetchone()
        return row is not None

    def get_by_day(self, animal_id: str, game_day: int) -> List[Reflection]:
        with get_conn() as conn:
            rows = conn.execute(
                """SELECT * FROM reflections
                   WHERE animal_id = ? AND game_day = ?
                   ORDER BY importance DESC""",
                (animal_id, game_day),
            ).fetchall()
        return [_row_to_refl(r) for r in rows]


def _row_to_refl(r) -> Reflection:
    tags = [t for t in (r["tags"] or "").split(",") if t]
    return Reflection(
        id=r["id"],
        animal_id=r["animal_id"],
        game_day=r["game_day"],
        content=r["content"],
        importance=r["importance"],
        tags=tags,
        created_at=r["created_at"],
    )


# ──────────────────────────────────────────────
# IntentStore
# ──────────────────────────────────────────────

@dataclass
class IntentEntry:
    id: int
    animal_id: str
    target_id: str          # 目标 NPC id，可为 ""
    intent_text: str
    game_day: int
    activate_hour: int
    consumed: bool
    created_at: int


class IntentStore:
    """animal_intents 表的 CRUD 接口。"""

    def add(
        self,
        animal_id: str,
        intent_text: str,
        game_day: int,
        target_id: str = "",
        activate_hour: int = 10,
    ) -> None:
        with get_conn() as conn:
            conn.execute(
                """INSERT INTO animal_intents
                   (animal_id, target_id, intent_text, game_day, activate_hour, consumed, created_at)
                   VALUES (?, ?, ?, ?, ?, 0, ?)""",
                (animal_id, target_id, intent_text, game_day, activate_hour, int(time.time())),
            )

    def pending(self, game_day: int, current_hour: int) -> List[IntentEntry]:
        """当日未执行、且激活时间已到的意图。"""
        with get_conn() as conn:
            rows = conn.execute(
                """SELECT * FROM animal_intents
                   WHERE game_day = ? AND consumed = 0 AND activate_hour <= ?
                   ORDER BY id""",
                (game_day, current_hour),
            ).fetchall()
        return [_row_to_intent(r) for r in rows]

    def mark_consumed(self, intent_id: int) -> None:
        with get_conn() as conn:
            conn.execute(
                "UPDATE animal_intents SET consumed = 1 WHERE id = ?",
                (intent_id,),
            )

    def has_intent_today(self, animal_id: str, game_day: int) -> bool:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM animal_intents WHERE animal_id = ? AND game_day = ? LIMIT 1",
                (animal_id, game_day),
            ).fetchone()
        return row is not None


def _row_to_intent(r) -> IntentEntry:
    return IntentEntry(
        id=r["id"],
        animal_id=r["animal_id"],
        target_id=r["target_id"] or "",
        intent_text=r["intent_text"],
        game_day=r["game_day"],
        activate_hour=r["activate_hour"],
        consumed=bool(r["consumed"]),
        created_at=r["created_at"],
    )


# ──────────────────────────────────────────────
# 每日反思（新版）
# ──────────────────────────────────────────────

DAILY_REFLECTION_PROMPT = """你是 {name}，一只 {species}，住在怪物森林里。今天（第 {game_day} 游戏日）结束了。

【今日记忆片段（时间正序）】
{memories_text}

【你听说的今日镇上动静】
{world_text}

【你对玩家目前的好感度】{affection_label}（数值 {affection_value}）

【怪物森林的居民名单（供填写 target_id 时参考）】
{npc_roster}

---
请以你（{name}）的第一视角，对今天做 3-5 条内心反思。
要求：
- 有情感倾向，不要只陈述事实
- 每条 20 字以内
- 可以包含对玩家的印象、对某件事的感受、明天想做什么（意图）

输出格式：严格一个 JSON 数组，不要有其他文字：
[
  {{"content": "反思内容", "importance": 7, "tags": ["player"]}},
  {{"content": "明天想去找老咸聊聊", "importance": 5, "tags": ["intent"], "target_id": "pirate_lao"}},
  ...
]

importance 1-10：1-3 琐事 / 4-6 有点意思 / 7-8 情感明显 / 9-10 人生级
tags 可选：player / gift / npc / event / intent / other
target_id：仅 tags 含 intent 时填写，从居民名单里选对应 id；若意图无特定对象则留空字符串""。"""


async def run_daily_reflection(
    animal_id: str,
    animal_name: str,
    animal_species: str,
    game_day: int,
    memory_store: MemoryStore,
    world_store,        # WorldEventStore
    affection_store,    # AffectionStore
    reflection_store: ReflectionStore,
    llm: LLMClient,
    *,
    npc_name_map: Dict[str, str] = {},   # {name: id} 供 intent target 解析
    intent_store: Optional[IntentStore] = None,
    force: bool = False,
) -> List[Reflection]:
    """游戏日 22:00 触发。force=True 跳过"今日已反思"检查（用于 debug）。"""
    if not force and reflection_store.has_reflected_today(animal_id, game_day):
        log.info("[reflect] %s day=%d 今日已反思，跳过", animal_id, game_day)
        return []

    # 收集今日记忆（最近 30 条，排除旧 reflection 类型）
    recent_mems = memory_store.recent(animal_id, n=30)
    today_mems = [m for m in recent_mems if m.type != "reflection"][:20]
    if not today_mems:
        log.info("[reflect] %s day=%d 无记忆，跳过", animal_id, game_day)
        return []

    memories_text = "\n".join(
        f"- [{m.game_time or '未知时刻'}][{m.type}] {m.content[:80]}"
        for m in reversed(today_mems)
    )

    # 世界事件
    world_events = world_store.recent(n=6, exclude_actor=animal_id)
    world_text = (
        "\n".join(f"- {e.description[:60]}" for e in world_events)
        if world_events
        else "（今日无特别动静）"
    )

    # 好感度
    aff_value = affection_store.get(animal_id)
    from affection import level_of, level_label as _label
    aff_label = _label(level_of(aff_value))

    # NPC 名单（给 LLM 填 target_id 用）
    npc_roster = "\n".join(
        f"  {npc_id}: {npc_name}" for npc_name, npc_id in npc_name_map.items()
        if npc_id != animal_id
    ) or "（无其他居民信息）"

    prompt = DAILY_REFLECTION_PROMPT.format(
        name=animal_name,
        species=animal_species,
        game_day=game_day,
        memories_text=memories_text,
        world_text=world_text,
        affection_label=aff_label,
        affection_value=aff_value,
        npc_roster=npc_roster,
    )

    try:
        raw = await llm.chat(
            [{"role": "user", "content": prompt}],
            max_tokens=700,
            temperature=0.75,
        )
    except Exception as e:
        log.warning("[reflect] %s LLM 失败: %s", animal_id, e)
        return []

    items = _parse_reflection_json(raw)
    if not items:
        log.warning("[reflect] %s JSON 解析失败，原始: %.200s", animal_id, raw)
        return []

    results: List[Reflection] = []
    for item in items[:5]:
        content = str(item.get("content", "")).strip()
        if not content:
            continue
        importance = max(1, min(10, int(item.get("importance", 5))))
        tags = item.get("tags", [])
        tags = [str(t) for t in tags] if isinstance(tags, list) else []
        reflection_store.add(animal_id, game_day, content, importance, tags)
        results.append(Reflection(
            id=-1, animal_id=animal_id, game_day=game_day,
            content=content, importance=importance,
            tags=tags, created_at=int(time.time()),
        ))

        # 提取意图：写入 animal_intents
        if "intent" in tags and intent_store is not None:
            target_id = str(item.get("target_id", "")).strip()
            # 安全校验：target_id 必须在已知 NPC 列表里
            known_ids = set(npc_name_map.values())
            if target_id and target_id not in known_ids:
                log.warning("[reflect] %s intent target_id '%s' 不在已知列表，忽略", animal_id, target_id)
                target_id = ""
            # 激活时间：当日游戏时间的 9-16 点之间随机（用 hash 保持确定性）
            activate_hour = 9 + (hash(f"{animal_id}{game_day}") % 8)
            intent_store.add(
                animal_id, content, game_day + 1,  # 意图在"明天"执行
                target_id=target_id,
                activate_hour=activate_hour,
            )
            log.info(
                "[reflect/intent] %s→%s day=%d h=%d: %s",
                animal_id, target_id or "?", game_day + 1, activate_hour, content[:30],
            )

    log.info(
        "[reflect] %s day=%d 生成 %d 条: %s",
        animal_id, game_day, len(results),
        [r.content[:20] for r in results],
    )

    # 清理旧记忆（每日反思后顺带一次，控制 DB 体积）
    deleted = memory_store.cleanup_old(animal_id)
    if deleted:
        log.info("[reflect/cleanup] %s 清理 %d 条低重要度旧记忆", animal_id, deleted)

    return results


def _parse_reflection_json(raw: str) -> List[dict]:
    """容错提取 LLM 输出里的 JSON 数组。"""
    raw = raw.strip()
    start = raw.find("[")
    end = raw.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        data = json.loads(raw[start:end + 1])
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


# ──────────────────────────────────────────────
# 旧版（累积记忆数触发，兼容现有对话后被动调用）
# ──────────────────────────────────────────────

_REFLECTION_PROMPT_OLD = """你是 {name}。请基于下面这些最近的对话和事件记忆，总结出 1-3 条高层观察或感想，作为你对这位玩家或周围发生事情的"反思"。

每条反思格式：一句话，第一人称（如"我感觉这个旅人..."、"似乎最近镇上..."）。
反思要简短、有人情味、可能影响你以后跟玩家相处的态度。
不要说"作为AI"。不要重复琐碎事实。
直接输出反思，每行一条，不要编号、不要解释。

【最近的记忆】
{memories_text}

【你的反思】"""


def _get_state(animal_id: str) -> tuple:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT last_reflect_at, last_memory_id FROM reflection_state WHERE animal_id = ?",
            (animal_id,),
        ).fetchone()
    return (row["last_reflect_at"], row["last_memory_id"]) if row else (0, 0)


def _set_state(animal_id: str, last_memory_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO reflection_state (animal_id, last_reflect_at, last_memory_id)
               VALUES (?, ?, ?)
               ON CONFLICT(animal_id) DO UPDATE SET
                 last_reflect_at = excluded.last_reflect_at,
                 last_memory_id  = excluded.last_memory_id""",
            (animal_id, int(time.time()), last_memory_id),
        )


async def reflect_if_needed(
    animal_id: str,
    animal_name: str,
    store: MemoryStore,
    llm: LLMClient,
    *,
    threshold: int = 12,
) -> List[str]:
    """累计 >=threshold 条新记忆触发反思（旧版，存入 memories 表 type=reflection）。"""
    _last_ts, last_id = _get_state(animal_id)
    new_memories = store.all_since(animal_id, after_id=last_id)
    if len(new_memories) < threshold:
        return []

    recent = new_memories[: min(threshold * 2, 30)]
    memories_text = "\n".join(
        f"[{m.game_time or '...'}][{m.type}] {m.speaker or ''}: {m.content}"
        for m in recent
    )
    prompt = _REFLECTION_PROMPT_OLD.format(name=animal_name, memories_text=memories_text)
    try:
        text = await llm.chat(
            [{"role": "user", "content": prompt}],
            max_tokens=300,
            temperature=0.7,
        )
    except Exception as e:
        log.warning("reflection(old) LLM 失败: %s", e)
        return []

    lines = [ln.strip().lstrip("-•·").strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln and len(ln) > 4][:3]
    if not lines:
        return []

    max_id = max(m.id for m in new_memories)
    for line in lines:
        store.add(animal_id, line, type="reflection", speaker="self", importance=8)
    _set_state(animal_id, max_id)
    log.info("[reflect_old] %s 生成 %d 条", animal_id, len(lines))
    return lines
