"""打听情报：玩家向 A 打听 B 的底细。

设计要点
--------
- 玩家不指定目标，由 A 自己挑一个人说（优先竞选对手 / 今天没被打听过的）。
- 情报按**受访者 A 对玩家的好感**分档解锁：交情越深，说得越多。
    好感 <10   只肯说传闻，或干脆敷衍
    好感 >=10  + 目标最爱的礼物
    好感 >=30  + 目标讨厌的礼物 + 目标对玩家的态度
    好感 >=55  + 目标的选情倾向（投谁）
- 每天每个 A 只能被打听一次（profile 记 last_intel_day）。

情报条目结构（下发给客户端）：
    {"icon": "🎁", "kind": "gift", "text": "…"}
"""
from __future__ import annotations

import random
from typing import Any, Dict, List, Optional

import items


# 好感解锁门槛
TIER_GIFT    = 10    # 说出「最爱的东西」
TIER_DISLIKE = 30    # 说出「讨厌的东西」+ 对玩家态度
TIER_VOTE    = 55    # 说出选情倾向


def _item_name(item_id: str) -> str:
    it = items.get(item_id)
    return it.name if it else item_id


def _attitude_word(value: int) -> str:
    if value >= 55:
        return "挺信任你的"
    if value >= 30:
        return "对你印象不错"
    if value >= 10:
        return "跟你还算熟"
    if value >= 0:
        return "对你没什么感觉"
    if value >= -20:
        return "有点提防你"
    return "相当不待见你"


def pick_target(
    speaker_id: str,
    all_ids: List[str],
    opponent_id: str,
    asked_today: set,
) -> Optional[str]:
    """A 自己挑一个要聊的对象：优先竞选对手，其次今天还没被聊过的。"""
    pool = [a for a in all_ids if a != speaker_id]
    if not pool:
        return None
    if opponent_id and opponent_id in pool and opponent_id not in asked_today:
        return opponent_id
    fresh = [a for a in pool if a not in asked_today]
    return random.choice(fresh) if fresh else random.choice(pool)


def build_tips(
    target_id: str,
    target_name: str,
    target_persona: Dict[str, Any],
    speaker_affection: int,
    target_affection: int,
    vote_pref: str,
    vote_pref_label: str,
    ties: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, str]]:
    """按受访者好感分档，组装可下发的情报条目。

    ties: 目标与他人的关系列表（已按亲疏降序），元素含 name/value/label。
          用于告诉玩家「该找谁传这话」——造谣的核心决策信息。
    """
    tips: List[Dict[str, str]] = []
    prefs = (target_persona.get("gift_prefs") or {})

    if speaker_affection >= TIER_GIFT:
        loves = list(prefs.get("loves") or [])
        if loves:
            picks = random.sample(loves, min(2, len(loves)))
            names = "、".join(_item_name(i) for i in picks)
            tips.append({
                "icon": "🎁", "kind": "gift",
                "text": "%s 最稀罕的是 %s，送这个准没错。" % (target_name, names),
            })

    if speaker_affection >= TIER_DISLIKE:
        bad = list(prefs.get("hates") or []) or list(prefs.get("dislikes") or [])
        if bad:
            names = _item_name(random.choice(bad))
            tips.append({
                "icon": "🚫", "kind": "dislike",
                "text": "千万别拿 %s 去找 %s，TA 见了就烦。" % (names, target_name),
            })
        tips.append({
            "icon": "💚", "kind": "attitude",
            "text": "我看 %s %s。" % (target_name, _attitude_word(target_affection)),
        })
        # 人际关系：谁护着 TA、谁跟 TA 不对付 —— 决定该找谁下手
        if ties:
            best = ties[0]
            tips.append({
                "icon": "🤝", "kind": "tie_good",
                "text": "%s 跟 %s %s，当着人家别说 %s 的不是。" % (
                    target_name, best["name"], best["label"], target_name),
            })
            bad = ties[-1]
            if bad["value"] < 0 and bad["name"] != best["name"]:
                tips.append({
                    "icon": "💢", "kind": "tie_bad",
                    "text": "不过 %s 跟 %s %s，那位可听不得 %s 的好话。" % (
                        target_name, bad["name"], bad["label"], target_name),
                })
            elif bad["value"] < 15 and bad["name"] != best["name"]:
                tips.append({
                    "icon": "💢", "kind": "tie_bad",
                    "text": "倒是 %s 跟 %s 不算亲近，那位未必肯替 TA 说话。" % (
                        target_name, bad["name"]),
                })

    if speaker_affection >= TIER_VOTE and vote_pref_label:
        tips.append({
            "icon": "🗳", "kind": "vote",
            "text": "悄悄告诉你，%s 现在更看好 %s。" % (target_name, vote_pref_label),
        })

    return tips


def tier_hint(speaker_affection: int) -> str:
    """还没解锁时给玩家的提示，告诉他怎么撬开嘴。"""
    if speaker_affection < TIER_GIFT:
        return "（跟 TA 更熟一些，才肯透露别人的喜好）"
    if speaker_affection < TIER_DISLIKE:
        return "（交情再深些，能问出更多）"
    if speaker_affection < TIER_VOTE:
        return "（成为知己后，连投票倾向都会告诉你）"
    return ""
