"""从玩家发言中规则提取关键事实。

简单正则 + 重要度评分。后续可换 LLM 兜底。
"""
from __future__ import annotations

import re
from typing import Dict


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
# 覆盖玩家发言（自我披露）、NPC 情感（强烈反应）、互动事件
_IMPORTANCE_BOOST = {
    # 玩家自我披露
    "重要": 2,
    "记住": 3,
    "秘密": 3,
    "妈妈": 1, "爸爸": 1, "家人": 1,
    "生病": 2, "去世": 3,
    "结婚": 2, "生日": 1,
    # 强烈情感（NPC 或玩家视角均适用）
    "惊喜": 2,
    "感动": 2,
    "难过": 2,
    "伤心": 2,
    "愤怒": 2,
    "生气": 2,
    "高兴": 1,
    "开心": 1,
    "讨厌": 1,
    "害怕": 1,
    "失望": 2,
    "感谢": 1,
    "谢谢": 1,
    "对不起": 2,
    "道歉": 2,
    "争吵": 3,
    "吵架": 3,
    "大吵": 3,
    "吵了": 2,
    "哭": 2,
    # 礼物/物品交换
    "送给": 1,
    "礼物": 1,
    "喜欢这个": 2,
    "讨厌这个": 2,
    "又来": 1,
    # 承诺/约定
    "答应": 2,
    "承诺": 3,
    "约定": 2,
}


def extract_facts(text: str) -> Dict[str, str]:
    """从一句话提事实。返回 {key: value} 字典。"""
    facts: Dict[str, str] = {}
    for pattern, key, _imp in _RULES:
        m = pattern.search(text)
        if m:
            value = m.group(m.lastindex or 1).strip()
            if value:
                if key not in facts:
                    facts[key] = value
    return facts


def estimate_importance(text: str, *, base: int = 4) -> int:
    """估算这句话的记忆重要度 1-10。

    base 调节基线：
      玩家发言 base=4（默认），NPC 普通对话 base=3，
      NPC↔NPC 闲聊 base=2，greet base=3。
    """
    score = base
    for word, boost in _IMPORTANCE_BOOST.items():
        if word in text:
            score += boost
    # 含规则提取到的事实 → 至少 7
    if extract_facts(text):
        score = max(score, 7)
    # 长度加成
    if len(text) > 40:
        score += 1
    return max(1, min(10, score))


def importance_for_gift(delta: int) -> int:
    """礼物事件的记忆重要度，按 affection delta 分 4 档。"""
    if delta >= 4:
        return 8   # 喜爱的东西，印象深刻
    if delta >= 1:
        return 6   # 普通正面礼物
    if delta == 0:
        return 3   # 疲劳/无感
    return 6       # 负面礼物也重要（记得对方的失礼）
