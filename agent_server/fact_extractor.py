"""从玩家发言中规则提取关键事实。

简单正则 + 重要度评分。后续可换 LLM 兜底。
"""
from __future__ import annotations

import re
from typing import Dict, Tuple


# (正则, key, 重要度) — 命中即写入 player_profile
_RULES: list = [
    (re.compile(r"我叫([^\s，。！？,.!?]{1,8})"), "name", 9),
    (re.compile(r"我是([^\s，。！？,.!?]{1,8})(?:[。，！？.,!?]|$)"), "name", 8),
    (re.compile(r"叫我([^\s，。！？,.!?]{1,8})"), "name", 8),
    (re.compile(r"我喜欢(.{1,20}?)(?:[。，！？.,!?]|$)"), "likes", 7),
    (re.compile(r"我爱(.{1,20}?)(?:[。，！？.,!?]|$)"), "likes", 7),
    (re.compile(r"我讨厌(.{1,20}?)(?:[。，！？.,!?]|$)"), "dislikes", 7),
    (re.compile(r"我害怕(.{1,20}?)(?:[。，！？.,!?]|$)"), "fears", 6),
    (re.compile(r"我家(?:住)?在(.{1,15}?)(?:[。，！？.,!?]|$)"), "home", 6),
    (re.compile(r"我(?:的)?生日是(.{1,15}?)(?:[。，！？.,!?]|$)"), "birthday", 8),
    (re.compile(r"我要去(找|见)(.{1,10}?)(?:[。，！？.,!?]|$)"), "intent", 6),
]


# 含这些词时整体重要度 +X
_IMPORTANCE_BOOST = {
    "重要": 2,
    "记住": 3,
    "秘密": 3,
    "妈妈": 1, "爸爸": 1, "家人": 1,
    "生病": 2, "去世": 3,
    "结婚": 2, "生日": 1,
}


def extract_facts(text: str) -> Dict[str, str]:
    """从一句话提事实。返回 {key: value} 字典。"""
    facts: Dict[str, str] = {}
    for pattern, key, _imp in _RULES:
        m = pattern.search(text)
        if m:
            # 多组的 group(2) / 单组 group(1)
            value = m.group(m.lastindex or 1).strip()
            if value:
                # 同 key 只取第一条命中（最强规则在前）
                if key not in facts:
                    facts[key] = value
    return facts


def estimate_importance(text: str, *, base: int = 4) -> int:
    """估算这句话对动物来说的重要度 1-10。"""
    score = base
    for word, boost in _IMPORTANCE_BOOST.items():
        if word in text:
            score += boost
    # 含规则提取到的事实 → 至少 7
    if extract_facts(text):
        score = max(score, 7)
    # 长度加成
    if len(text) > 30:
        score += 1
    return max(1, min(10, score))
