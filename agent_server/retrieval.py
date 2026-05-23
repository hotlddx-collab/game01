"""记忆检索：时近 × 重要度 × 关键词相关。"""
from __future__ import annotations

import math
import re
import time
from typing import List, Set

from memory import Memory, MemoryStore


# 时近衰减：12 小时半衰
RECENCY_HALF_LIFE_SEC = 12 * 3600.0
# 权重
W_RECENCY = 0.45
W_IMPORTANCE = 0.30
W_KEYWORD = 0.25


def _tokens(text: str) -> Set[str]:
    """简单 tokenize：去标点、按字符 + 短词。中文按字符。"""
    cleaned = re.sub(r"[^\w\u4e00-\u9fff]+", " ", text.lower())
    chars = set()
    for w in cleaned.split():
        if not w:
            continue
        # 中文字符当作单字 token；英文整词
        if re.match(r"[a-z0-9_]+", w):
            chars.add(w)
        else:
            for ch in w:
                if "\u4e00" <= ch <= "\u9fff":
                    chars.add(ch)
    return chars


def _score(memory: Memory, query_tokens: Set[str], now_ts: int) -> float:
    # 1. 时近：exp 衰减
    age_sec = max(now_ts - memory.real_time, 0)
    recency = math.exp(-age_sec / RECENCY_HALF_LIFE_SEC)

    # 2. 重要度归一化 0-1
    importance = max(min(memory.importance, 10), 1) / 10.0

    # 3. 关键词命中：jaccard 简化
    if not query_tokens:
        keyword = 0.0
    else:
        m_tokens = _tokens(memory.content)
        if not m_tokens:
            keyword = 0.0
        else:
            inter = len(query_tokens & m_tokens)
            keyword = inter / max(len(query_tokens), 1)

    # 反思类记忆稍加成
    bonus = 0.05 if memory.type == "reflection" else 0.0

    return W_RECENCY * recency + W_IMPORTANCE * importance + W_KEYWORD * keyword + bonus


def retrieve_relevant(
    store: MemoryStore,
    animal_id: str,
    query: str,
    *,
    candidate_n: int = 60,
    top_k: int = 5,
) -> List[Memory]:
    """从最近 candidate_n 条里挑 top_k 最相关的（按综合分）。"""
    candidates = store.recent(animal_id, n=candidate_n)
    if not candidates:
        return []
    q_tokens = _tokens(query)
    now_ts = int(time.time())
    scored = sorted(
        candidates,
        key=lambda m: _score(m, q_tokens, now_ts),
        reverse=True,
    )
    return scored[:top_k]
