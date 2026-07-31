"""NPC 问答：关系临近突破时，NPC 反过来考玩家是否真的了解自己。

设计
----
- 触发时机：好感**逼近**里程碑阈值（warm=5 / like=15 / love=30）前 3 点内，
  按概率发问。答对 → 直接跨过门槛；答错 → 退回去，得再刷。
- 题目来自 persona.quiz（每 NPC 2 题），四选一（1 正确 + 3 干扰，选项打乱）。
- 每题每 NPC 只考一次，答过（无论对错）记进 profile，不重复。
- 线索三路可得：打听情报、NPC 捡东西时的观察文案（clue）、NPC 互聊台词（hint_topic）。
"""
from __future__ import annotations

import random
from typing import Any, Dict, List, Optional


# 好感里程碑阈值（与 affection._LEVELS 对齐）
MILESTONES = (5, 15, 30)

# 距离阈值多少点以内算「临近突破」
NEAR_RANGE = 3

# 触发概率（临近且有未考题目时）
TRIGGER_CHANCE = 0.55
# 观察线索：NPC 捡东西时露出癖好的概率。太高会刷屏，太低玩家攒不齐信息。
CLUE_CHANCE = 0.3
# 互聊时把爱好话题带进台词的概率
HINT_CHANCE = 0.35

# 答题好感增减
REWARD_CORRECT = 6
PENALTY_WRONG = -3

_ASKED_KEY_PREFIX = "quiz_asked_"


def is_near_milestone(value: int) -> bool:
    """好感是否正卡在某个里程碑门槛前的 NEAR_RANGE 点内。"""
    for m in MILESTONES:
        if m - NEAR_RANGE <= value < m:
            return True
    return False


def _asked_key(quiz_id: str) -> str:
    return _ASKED_KEY_PREFIX + quiz_id


def pick_quiz(
    animal_id: str,
    persona: Dict[str, Any],
    profile_store,
) -> Optional[Dict[str, Any]]:
    """挑一道该 NPC 还没考过的题；全考过返回 None。"""
    pool = persona.get("quiz") or []
    fresh = [
        q for q in pool
        if profile_store.get(animal_id, _asked_key(str(q.get("id", "")))) is None
    ]
    return random.choice(fresh) if fresh else None


def should_ask(
    animal_id: str,
    persona: Dict[str, Any],
    affection_value: int,
    profile_store,
    rng: random.Random | None = None,
) -> Optional[Dict[str, Any]]:
    """综合判断是否发问；要问则返回组装好的题目包，否则 None。"""
    if not is_near_milestone(affection_value):
        return None
    r = rng or random
    if r.random() > TRIGGER_CHANCE:
        return None
    q = pick_quiz(animal_id, persona, profile_store)
    if q is None:
        return None
    return build_payload(q, r)


def build_payload(q: Dict[str, Any], rng: random.Random | None = None) -> Dict[str, Any]:
    """把题目组装成下发给客户端的格式（选项已打乱）。"""
    r = rng or random
    options = [str(q.get("answer", ""))] + [str(w) for w in (q.get("wrong") or [])]
    options = [o for o in options if o]
    r.shuffle(options)
    return {
        "quiz_id": str(q.get("id", "")),
        "question": str(q.get("question", "")),
        "options": options,
        "answer": str(q.get("answer", "")),
    }


def judge(
    animal_id: str,
    persona: Dict[str, Any],
    quiz_id: str,
    chosen: str,
    profile_store,
) -> Dict[str, Any]:
    """判定玩家作答。返回 {correct, answer, delta, already}。"""
    pool = persona.get("quiz") or []
    q = next((x for x in pool if str(x.get("id", "")) == quiz_id), None)
    if q is None:
        return {"correct": False, "answer": "", "delta": 0, "already": True}
    # 已答过 → 不重复结算
    if profile_store.get(animal_id, _asked_key(quiz_id)) is not None:
        return {"correct": False, "answer": str(q.get("answer", "")),
                "delta": 0, "already": True}
    answer = str(q.get("answer", ""))
    correct = chosen.strip() == answer
    profile_store.set(animal_id, _asked_key(quiz_id), "1" if correct else "0")
    return {
        "correct": correct,
        "answer": answer,
        "delta": REWARD_CORRECT if correct else PENALTY_WRONG,
        "already": False,
    }


def clue_for(persona: Dict[str, Any], rng: random.Random | None = None) -> str:
    """随机取一条观察线索文案（NPC 捡东西 / 路过时冒出来）。"""
    pool = [str(q.get("clue", "")) for q in (persona.get("quiz") or []) if q.get("clue")]
    if not pool:
        return ""
    return (rng or random).choice(pool)


def hint_topics(persona: Dict[str, Any]) -> List[str]:
    """该 NPC 的爱好关键词，供互聊 prompt 引导带出。"""
    return [str(q.get("hint_topic", "")) for q in (persona.get("quiz") or []) if q.get("hint_topic")]
