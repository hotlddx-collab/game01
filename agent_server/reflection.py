"""反思机制：每隔一段记忆数，让 LLM 总结生成高层"反思"。"""
from __future__ import annotations

import logging
import time
from typing import List

from db import get_conn
from memory import Memory, MemoryStore
from llm import LLMClient


log = logging.getLogger("reflection")


REFLECTION_PROMPT = """你是 {name}。请基于下面这些最近的对话和事件记忆，总结出 1-3 条高层观察或感想，作为你对这位玩家或周围发生事情的"反思"。

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
    if row is None:
        return (0, 0)
    return (row["last_reflect_at"], row["last_memory_id"])


def _set_state(animal_id: str, last_memory_id: int) -> None:
    now_ts = int(time.time())
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO reflection_state (animal_id, last_reflect_at, last_memory_id)
               VALUES (?, ?, ?)
               ON CONFLICT(animal_id) DO UPDATE SET
                 last_reflect_at = excluded.last_reflect_at,
                 last_memory_id = excluded.last_memory_id""",
            (animal_id, now_ts, last_memory_id),
        )


async def reflect_if_needed(
    animal_id: str,
    animal_name: str,
    store: MemoryStore,
    llm: LLMClient,
    *,
    threshold: int = 12,
) -> List[str]:
    """累计 >=threshold 条新记忆触发反思。返回新生成的 reflection 文本列表。"""
    _last_ts, last_id = _get_state(animal_id)
    new_memories = store.all_since(animal_id, after_id=last_id)
    if len(new_memories) < threshold:
        return []

    # 取最近的部分供反思（避免太多）
    recent = new_memories[: min(threshold * 2, 30)]
    memories_text = "\n".join(
        f"[{m.game_time or '...'}][{m.type}] {m.speaker or ''}: {m.content}" for m in recent
    )

    prompt = REFLECTION_PROMPT.format(name=animal_name, memories_text=memories_text)
    try:
        text = await llm.chat(
            [{"role": "user", "content": prompt}],
            max_tokens=300,
            temperature=0.7,
        )
    except Exception as e:
        log.warning("reflection LLM 失败: %s", e)
        return []

    # 拆分多行反思
    lines = [ln.strip().lstrip("-•·").strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln and len(ln) > 4][:3]

    if not lines:
        return []

    max_id = max(m.id for m in new_memories)
    for line in lines:
        store.add(
            animal_id,
            line,
            type="reflection",
            speaker="self",
            importance=8,
        )
    _set_state(animal_id, max_id)
    log.info("[reflection] %s 生成 %d 条: %s", animal_id, len(lines), lines)
    return lines
