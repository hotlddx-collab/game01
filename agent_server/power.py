"""镇长任期权力点行动（D9）。

设计参考 docs/mayor_loop.md §7。

v1 实现两种行动：
- 巡视拜访 visit（花 1 点）：对指定 NPC +好感，写一条「定向」world_event（仅该 NPC 的 event 子项吃到）。
- 发布公告 announce（花 2 点）：对全体 voter 小幅 +好感，写一条「全镇」world_event（actor=player → 所有 voter event 子项吃到）。

权力点由 ElectionStore 管理（每日 06:00 补满、不累积、仅现任）。
本模块只负责「花点 + 产生效果」，扣点交给 ElectionStore.spend_power_points。
"""
from __future__ import annotations

import logging
import random
from typing import Any, Dict, List, Optional

import items as items_module

log = logging.getLogger("power")

# 行动定义（供 UI 渲染 + 校验）
ACTIONS: Dict[str, Dict[str, Any]] = {
    "visit": {
        "label": "巡视拜访",
        "cost": 1,
        "need_target": True,
        "desc": "登门看望一位居民，提升其好感（+5）",
    },
    "announce": {
        "label": "发布公告",
        "cost": 2,
        "need_target": False,
        "desc": "面向全镇发表施政公告，全员小幅好感（+2）",
    },
    "demand": {
        "label": "索取",
        "cost": 1,
        "need_target": True,
        "desc": "以镇长身份向居民索取一件东西（非其心爱之物），会掉好感",
    },
}

VISIT_AFFECTION = 5
ANNOUNCE_AFFECTION = 2
DEMAND_AFFECTION = -8

FALLBACK_VISIT_LINES = [
    "镇长亲自登门，问起了你最近的近况。",
    "镇长来看望你，承诺会把你的难处放在心上。",
    "镇长拎着点心上门，陪你聊了好一会儿。",
]
FALLBACK_ANNOUNCE_LINES = [
    "镇长在广场发表公告，承诺让小镇日子更红火。",
    "镇长召集大家，宣布了新的施政打算，居民们议论纷纷。",
]
FALLBACK_DEMAND_LINES = [
    "镇长开了口，你只好把东西递了过去，心里有点不是滋味。",
    "拗不过镇长的身份，东西还是交出去了，脸色却不太好看。",
    "镇长说要，你也不好当面驳回，闷闷地给了。",
]


class PowerManager:
    """权力点行动执行器。"""

    def __init__(
        self,
        election_store,
        affection_store,
        world_store,
        personas: Dict[str, Dict[str, Any]],
        llm=None,
    ) -> None:
        self.election = election_store
        self.affection = affection_store
        self.world = world_store
        self.personas = personas
        self.llm = llm

    def _name(self, npc_id: str) -> str:
        return self.personas.get(npc_id, {}).get("name", npc_id)

    async def perform(
        self,
        term: Dict,
        game_day: int,
        action: str,
        target_id: str = "",
    ) -> Dict[str, Any]:
        """执行权力行动。返回 {ok, error?, action, spent, affected:[...], text}。"""
        spec = ACTIONS.get(action)
        if spec is None:
            return {"ok": False, "error": f"未知行动 {action}"}

        term_id = int(term["term_id"])
        # 仅玩家现任可用
        if not self.election.is_incumbent(term_id, "player"):
            return {"ok": False, "error": "你不是现任镇长，无法使用权力点"}

        # 校验目标
        voters = self.election.voters_of(term)
        if spec["need_target"]:
            if target_id not in voters:
                return {"ok": False, "error": "目标无效（只能对本镇居民使用）"}

        # 索取要先确认对方手上有拿得出手的东西，免得点数白扣却两手空空
        if action == "demand" and self._pick_demand_item(target_id) is None:
            return {"ok": False, "error": f"{self._name(target_id)}身上没有能索取的东西"}

        # 扣点（先确保跨日补满）
        self.election.refresh_power_points(term_id, "player", game_day)
        if not self.election.spend_power_points(term_id, "player", int(spec["cost"])):
            return {"ok": False, "error": "权力点不足"}

        if action == "visit":
            return await self._do_visit(term, game_day, target_id)
        if action == "demand":
            return await self._do_demand(term, game_day, target_id)
        return await self._do_announce(term, game_day)

    async def _do_visit(self, term: Dict, game_day: int, target_id: str) -> Dict[str, Any]:
        name = self._name(target_id)
        snap = self.affection.adjust(target_id, VISIT_AFFECTION)
        line = await self._gen_visit_line(target_id)
        # 定向事件：actor 留空 + 描述含 voter_id + "玩家" + 正面词 → 仅该 voter 的 event 子项命中
        desc = "玩家以镇长身份巡视拜访了 %s（%s），%s 感到被支持。原话：%s" % (
            name, target_id, name, line,
        )
        if self.world is not None:
            self.world.add(actor="", description=desc)
        log.info("[power] visit target=%s aff=%s", target_id, snap.get("value"))
        return {
            "ok": True,
            "action": "visit",
            "spent": ACTIONS["visit"]["cost"],
            "affected": [{
                "npc_id": target_id,
                "name": name,
                "affection": int(snap.get("value", 0)),
                "level": snap.get("level", "neutral"),
                "delta": VISIT_AFFECTION,
            }],
            "text": line,
        }

    async def _do_announce(self, term: Dict, game_day: int) -> Dict[str, Any]:
        voters = self.election.voters_of(term)
        affected: List[Dict[str, Any]] = []
        for v in voters:
            snap = self.affection.adjust(v, ANNOUNCE_AFFECTION)
            affected.append({
                "npc_id": v,
                "name": self._name(v),
                "affection": int(snap.get("value", 0)),
                "level": snap.get("level", "neutral"),
                "delta": ANNOUNCE_AFFECTION,
            })
        line = await self._gen_announce_line(term)
        # 全镇事件：actor=player → 所有 voter 的 event 子项命中（actor==candidate 分支）
        desc = "玩家以镇长身份发布全镇公告，承诺解决大家关心的事，赢得支持。原话：%s" % line
        if self.world is not None:
            self.world.add(actor="player", description=desc)
        log.info("[power] announce voters=%d", len(voters))
        return {
            "ok": True,
            "action": "announce",
            "spent": ACTIONS["announce"]["cost"],
            "affected": affected,
            "text": line,
        }

    def _pick_demand_item(self, target_id: str) -> Optional[Dict[str, str]]:
        """在该 NPC 的回礼池里挑一件东西索取，招牌物（signature_gift）排除在外——
        那是他最珍视的东西，镇长权力也不能强要。"""
        persona = self.personas.get(target_id, {})
        sig = persona.get("signature_gift", "")
        pool: List[str] = []
        for field in ("return_gifts", "mid_return_gifts", "rare_return_gifts"):
            pool.extend(persona.get(field) or [])
        pool = [iid for iid in dict.fromkeys(pool) if iid and iid != sig]
        random.shuffle(pool)
        for item_id in pool:
            item = items_module.get(item_id)
            if item is not None:
                return {"item_id": item_id, "item_name": item.name}
        return None

    async def _do_demand(self, term: Dict, game_day: int, target_id: str) -> Dict[str, Any]:
        name = self._name(target_id)
        picked = self._pick_demand_item(target_id)
        if picked is None:
            return {"ok": False, "error": f"{name}身上没有能索取的东西"}
        snap = self.affection.adjust(target_id, DEMAND_AFFECTION)
        line = await self._gen_demand_line(target_id, picked["item_name"])
        desc = "玩家以镇长身份向 %s（%s）索取了%s，%s心里不太痛快。原话：%s" % (
            name, target_id, picked["item_name"], name, line,
        )
        if self.world is not None:
            self.world.add(actor="", description=desc)
        log.info("[power] demand target=%s item=%s aff=%s",
                 target_id, picked["item_id"], snap.get("value"))
        return {
            "ok": True,
            "action": "demand",
            "spent": ACTIONS["demand"]["cost"],
            "item_id": picked["item_id"],
            "item_name": picked["item_name"],
            "affected": [{
                "npc_id": target_id,
                "name": name,
                "affection": int(snap.get("value", 0)),
                "level": snap.get("level", "neutral"),
                "delta": DEMAND_AFFECTION,
            }],
            "text": "%s（获得 %s，%s -%d 好感）" % (
                line, picked["item_name"], name, abs(DEMAND_AFFECTION)),
        }

    # ---- LLM 台词（带兜底）----

    async def _gen_visit_line(self, target_id: str) -> str:
        if self.llm is None:
            return random.choice(FALLBACK_VISIT_LINES)
        persona = self.personas.get(target_id, {})
        sys = (
            "你帮玩家（新任镇长）生成一句巡视拜访某居民时说的暖心话。"
            "不超过 30 字，口语、真诚，直接说话不要旁白引号。"
        )
        user = "居民：%s（%s）。镇长上门看望，说一句：" % (
            persona.get("name", target_id), persona.get("personality", ""),
        )
        try:
            resp = await self.llm.chat(
                messages=[{"role": "system", "content": sys}, {"role": "user", "content": user}],
                max_tokens=60, temperature=0.85,
            )
            line = (resp or "").strip().strip("「」\"'")
            if line:
                return line[:60]
        except Exception as e:
            log.warning("[power] visit line LLM 失败: %s", e)
        return random.choice(FALLBACK_VISIT_LINES)

    async def _gen_announce_line(self, term: Dict) -> str:
        if self.llm is None:
            return random.choice(FALLBACK_ANNOUNCE_LINES)
        sys = (
            "你帮玩家（现任镇长）生成一句面向全镇的施政公告。"
            "不超过 35 字，有领导口吻但亲切，直接说话不要旁白引号。"
        )
        try:
            resp = await self.llm.chat(
                messages=[{"role": "system", "content": sys},
                          {"role": "user", "content": "镇长发布公告，说一句："}],
                max_tokens=70, temperature=0.85,
            )
            line = (resp or "").strip().strip("「」\"'")
            if line:
                return line[:70]
        except Exception as e:
            log.warning("[power] announce line LLM 失败: %s", e)
        return random.choice(FALLBACK_ANNOUNCE_LINES)

    async def _gen_demand_line(self, target_id: str, item_name: str) -> str:
        if self.llm is None:
            return random.choice(FALLBACK_DEMAND_LINES)
        persona = self.personas.get(target_id, {})
        sys = (
            "你帮一位居民生成一句被镇长开口索取东西时的反应台词，心里不情愿但拗不过身份，"
            "不超过 30 字，口语、直接说话不要旁白引号。"
        )
        user = "居民：%s（%s）。镇长向你索取了%s，你不情不愿地嘀咕一句：" % (
            persona.get("name", target_id), persona.get("personality", ""), item_name,
        )
        try:
            resp = await self.llm.chat(
                messages=[{"role": "system", "content": sys}, {"role": "user", "content": user}],
                max_tokens=60, temperature=0.85,
            )
            line = (resp or "").strip().strip("「」\"'")
            if line:
                return line[:60]
        except Exception as e:
            log.warning("[power] demand line LLM 失败: %s", e)
        return random.choice(FALLBACK_DEMAND_LINES)
