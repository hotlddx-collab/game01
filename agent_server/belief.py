"""谣言信念判定：NPC 听到一条谣言后，信还是不信。

规则（纯数值，不走 LLM，保证快且可预期）：

    信任分 = 亲信度(传谣者→听者) − 护主度(被造谣者→听者) + 随机扰动

- 亲信度：听者和传谣者关系越好，越愿意信 ta 的话。
- 护主度：听者和被造谣者关系越好，越不信这种坏话；
          关系为负则反过来——坏话正中下怀，更容易被采信。
- 随机扰动：保留不确定性，避免玩家把结果算死。

关系取自 relations.RelationStore 的连续值（-100~100），
不再是布尔好友集合，故成功率是一条平滑曲线而非 0%/100% 硬切。

判定结果写入 rumor_belief，每人每条只判一次（判定即锁定）：
重复对同一 NPC 造同一话题不会重复扣分。
"""
from __future__ import annotations

import logging
import random
from typing import Dict, Optional

log = logging.getLogger("belief")

# ---- 可调参数 ----
BELIEVE_THRESHOLD = 0.0    # 信任分 > 阈值 → 相信
RANDOM_SPAN = 18.0         # 随机扰动幅度 ±
PLAYER_AFF_SCALE = 0.38    # 玩家好感（-50~100）折算到信任分的系数
NPC_BASE_TRUST = 12.0      # NPC 之间的默认基础信任（同镇熟人）
REJECT_AFF_PENALTY = -2    # 不信时，听者对造谣者的好感惩罚

# 关系值（-100~100）折算系数。原先是布尔的 ±45/±20 硬切，
# 造成造谣非 0% 即 100%；改为连续折算后中间地带才成立。
# 护主系数刻意高于玩家好感系数：关系铁的人再怎么讨好也撬不动，
# 玩家必须挑关系薄弱处下手，而不是靠堆好感全镇通吃。
REL_RESIST_SCALE = 0.55    # 护主度：听者越亲近被造谣者，越不信坏话
REL_TRUST_SCALE = 0.22     # 亲信度：听者越亲近传谣者，越愿意信

# 关系为负时，听者本就看目标不顺眼 → 坏话正中下怀，额外加成
GRUDGE_BONUS_SCALE = 0.25


def judge(
    listener_id: str,
    source_id: str,
    subject_id: str,
    sentiment: str,
    affection_store,
    relation_store,
    rng: Optional[random.Random] = None,
) -> Dict:
    """返回 {"believe": bool, "score": float, "reason": str}。

    listener_id : 听到谣言的 NPC
    source_id   : 谁说的（'player' 或 animal_id）
    subject_id  : 谣言主角（被造谣者，'player' 或 animal_id）
    sentiment   : smear / praise
    """
    r = rng or random
    score = 0.0
    reasons = []

    # ---- 1. 亲信度：听者有多信任「说这话的人」----
    if source_id == "player":
        aff = affection_store.get(listener_id)
        score += aff * PLAYER_AFF_SCALE
        if aff >= 30:
            reasons.append("信任你")
        elif aff <= -5:
            reasons.append("不信任你")
    else:
        score += NPC_BASE_TRUST
        rel = relation_store.get(listener_id, source_id)
        score += rel * REL_TRUST_SCALE
        if rel >= 30:
            reasons.append("信任朋友")
        elif rel <= -20:
            reasons.append("不待见传话的")

    # ---- 2. 护主度：听者有多护「被说的人」----
    if subject_id == "player":
        # 说玩家坏话 → 听者对玩家好感越高越不信
        aff_subj = affection_store.get(listener_id)
        score -= aff_subj * PLAYER_AFF_SCALE
        if aff_subj >= 30:
            reasons.append("护着你")
    else:
        rel_subj = relation_store.get(listener_id, subject_id)
        if rel_subj >= 0:
            score -= rel_subj * REL_RESIST_SCALE
            if rel_subj >= 60:
                reasons.append("死护着")
            elif rel_subj >= 30:
                reasons.append("护主")
        else:
            # 关系本就差 → 这种坏话他早就想听了
            score += (-rel_subj) * GRUDGE_BONUS_SCALE
            reasons.append("本来就有嫌隙")

    # ---- 3. 随机扰动 ----
    score += r.uniform(-RANDOM_SPAN, RANDOM_SPAN)

    believe = score > BELIEVE_THRESHOLD
    reason = "、".join(reasons) if reasons else ("将信将疑" if believe else "半信半疑")
    return {"believe": believe, "score": round(score, 1), "reason": reason}
