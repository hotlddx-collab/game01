"""物品来源索引：某件东西「谁手上有」「哪儿能捡到」。

存在的理由
----------
早先任务描述里的来源提示是**手写死的文案**（如小蓝要水壶却写「找小翠讨一只」），
与真实回礼配置毫无校验关系。实际持有水壶的是煊赫，小翠三档池子里从来没有水壶，
玩家照着提示跑断腿也拿不到——这是纯粹的误导。

本模块从 persona 的三档回礼池反查真实持有者，让提示与索要校验共用同一份事实，
配置改了提示自动跟着变，不会再对不上。

来源分三种：
- ``gift``   ：送该 NPC 喜欢的东西，他可能回赠（受每日一次限制）
- ``request``：好感够高时直接开口讨要（见 requests.py）
- ``ground`` ：地图上能捡到（base_value <= 3 的低价物）
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import items as items_module

log = logging.getLogger("items_source")

# 三档回礼池字段 → 解锁所需好感等级
TIER_FIELDS = (
    ("return_gifts", "common", ("neutral", "friendly", "fond", "close", "intimate")),
    ("mid_return_gifts", "mid", ("fond", "close", "intimate")),
    ("rare_return_gifts", "rare", ("close", "intimate")),
)

_LEVEL_LABEL = {
    "common": "关系一般就肯给",
    "mid": "得处成朋友才肯拿出来",
    "rare": "非挚交不轻易示人",
    "signature": "那是他的心爱之物，得交情极深才肯割爱",
}


class ItemSourceIndex:
    """item_id → 持有它的 NPC 列表（含档位）。"""

    def __init__(self, personas: Dict[str, Dict[str, Any]]) -> None:
        self.personas = personas
        self._index: Dict[str, List[Dict[str, str]]] = {}
        self._build()

    def rebuild(self, personas: Dict) -> None:
        """换届轮换后重建索引：离镇者不该再出现在「谁有这东西」的提示里。"""
        self.personas = personas
        self._build()

    def _build(self) -> None:
        self._index = {}
        for npc_id, persona in (self.personas or {}).items():
            gp = persona.get("gift_prefs", {}) or {}
            # 与 agent._filter_self_liked 保持一致：自己喜欢的东西不会送出去
            mine = set(gp.get("loves", []) or []) | set(gp.get("likes", []) or [])
            for field, tier, _levels in TIER_FIELDS:
                for item_id in (persona.get(field) or []):
                    if item_id in mine:
                        continue
                    self._index.setdefault(item_id, []).append({
                        "npc_id": npc_id,
                        "name": persona.get("name", npc_id),
                        "tier": tier,
                    })
            # 招牌物：该 NPC 的身份象征（邮差的信、旅人的地图碎片、煊赫的萤石）。
            # 这类东西不进回礼池（他自己也珍视，随机回赠会被玩家原样送回来刷好感），
            # 只能靠**开口索要**取得——玩家得明确说出想要什么，且交情极深。
            # 故这里不套用 mine 过滤：索要是一次性的定向行为，不构成刷分循环。
            sig = persona.get("signature_gift")
            if sig:
                self._index.setdefault(sig, []).append({
                    "npc_id": npc_id,
                    "name": persona.get("name", npc_id),
                    "tier": "signature",
                })
        log.info("[items_source] 索引建立完成：%d 件物品有 NPC 来源", len(self._index))

    def holders(self, item_id: str) -> List[Dict[str, str]]:
        """谁手上有这件东西。返回 [{npc_id, name, tier}]。"""
        return list(self._index.get(item_id, []))

    def holder_of(self, item_id: str, exclude: str = "") -> Optional[Dict[str, str]]:
        """挑一个持有者做提示，优先门槛最低的（最容易要到）。"""
        order = {"common": 0, "mid": 1, "rare": 2, "signature": 3}
        pool = [h for h in self.holders(item_id) if h["npc_id"] != exclude]
        if not pool:
            return None
        return sorted(pool, key=lambda h: order.get(h["tier"], 9))[0]

    def tier_of(self, item_id: str, npc_id: str) -> Optional[str]:
        """某 NPC 手上这件东西属于哪一档；他没有则 None。"""
        for h in self.holders(item_id):
            if h["npc_id"] == npc_id:
                return h["tier"]
        return None

    @staticmethod
    def is_ground_item(item_id: str) -> bool:
        """地图上能不能捡到。铁律：base_value <= 3 的才散落在地。"""
        item = items_module.get(item_id)
        return bool(item and item.base_value <= 3)

    def hint_for(self, item_id: str, asker_id: str = "") -> str:
        """生成一句**真实**的来源提示，供任务描述运行时拼接。"""
        item = items_module.get(item_id)
        name = item.name if item else item_id
        if self.is_ground_item(item_id):
            return "（%s 这东西林子里转转就能捡着）" % name
        holder = self.holder_of(item_id, exclude=asker_id)
        if holder is None:
            return "（%s 不好找，镇上未必有人肯给）" % name
        return "（听说 %s 手里有 %s，%s）" % (
            holder["name"], name, _LEVEL_LABEL.get(holder["tier"], ""))

    def unreachable_items(self, needed: List[str]) -> List[str]:
        """体检用：这些物品里哪些既捡不到、也没有任何 NPC 能给。"""
        out: List[str] = []
        for item_id in needed:
            if self.is_ground_item(item_id):
                continue
            if not self.holders(item_id):
                out.append(item_id)
        return out
