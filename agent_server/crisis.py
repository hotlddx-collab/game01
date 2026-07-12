"""危机调解玩法（镇长政务）。

设计参考本次会话「危机调解 · 设计稿」。

镇上不时爆发 NPC 之间的纠纷/突发事件（从 data/world/crises.json 抽取）。
玩家以镇长/候选人身份介入，选择一个调解方案 → 结算多方好感 +
写 world_event（喂选举 event 子项）+ 写 NPC 记忆 + 消耗道具。

事实（涉事方、经过、选项后果）由静态模板定死，防 LLM 乱编、保证公平；
仅「双方说法」与「对裁决的反应」交给 LLM，兼顾一致性与活人感。
"""
from __future__ import annotations

import json
import logging
import random
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from db import get_conn

log = logging.getLogger("crisis")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CRISES_FILE = PROJECT_ROOT / "data" / "world" / "crises.json"

# 软性截止：危机爆发后若 N 个游戏小时内未处理，自动按「不管」结算
DEADLINE_GAME_HOURS = 6


class CrisisManager:
    """危机事件的抽取、展示、调解结算。"""

    def __init__(
        self,
        election_store,
        affection_store,
        world_store,
        personas: Dict[str, Dict[str, Any]],
        memory_store=None,
        llm=None,
    ) -> None:
        self.election = election_store
        self.affection = affection_store
        self.world = world_store
        self.personas = personas
        self.memory = memory_store
        self.llm = llm
        self.defs: Dict[str, Dict[str, Any]] = self._load_defs()

    def _load_defs(self) -> Dict[str, Dict[str, Any]]:
        try:
            raw = json.loads(CRISES_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning("[crisis] 加载危机池失败: %s", e)
            return {}
        return {k: v for k, v in raw.items() if not k.startswith("_")}

    def _name(self, npc_id: str) -> str:
        return self.personas.get(npc_id, {}).get("name", npc_id)

    def _player_title(self) -> str:
        """玩家当前身份称谓：现任镇长 → 镇长；否则 → 候选人。"""
        try:
            term = self.election.get_active_term()
            if term and self.election.is_incumbent(int(term["term_id"]), "player"):
                return "镇长"
        except Exception:
            pass
        return "候选人"

    # ---- 查询 ----

    def get_active(self) -> Optional[Dict[str, Any]]:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM crisis_state WHERE status = 'active' ORDER BY crisis_id DESC LIMIT 1"
            ).fetchone()
        if not row:
            return None
        return self._row_to_view(dict(row))

    def _row_to_view(self, row: Dict[str, Any]) -> Dict[str, Any]:
        tpl = self.defs.get(row["template_id"], {})
        parties = tpl.get("parties", [])
        statements = {}
        if row.get("statements_json"):
            try:
                statements = json.loads(row["statements_json"])
            except Exception:
                statements = {}
        return {
            "crisis_id": int(row["crisis_id"]),
            "template_id": row["template_id"],
            "status": row["status"],
            "title": tpl.get("title", row["template_id"]),
            "scene": tpl.get("scene", ""),
            "summary": tpl.get("summary", ""),
            "parties": [{"npc_id": p, "name": self._name(p)} for p in parties],
            "statements": statements,   # {npc_id: 台词}
            "options": [
                {
                    "id": o.get("id", ""),
                    "label": o.get("label", ""),
                    "tag": o.get("tag", ""),
                    "requires": o.get("requires", {}) or {},
                }
                for o in tpl.get("options", [])
            ],
        }

    # ---- 抽取 ----

    def _cooldown_ok(self, template_id: str, game_day: int, cooldown_days: int) -> bool:
        if cooldown_days <= 0:
            return True
        with get_conn() as conn:
            row = conn.execute(
                """SELECT MAX(game_day) AS d FROM crisis_state
                   WHERE template_id = ? AND status = 'resolved'""",
                (template_id,),
            ).fetchone()
        last = row["d"] if row and row["d"] is not None else None
        if last is None:
            return True
        return game_day - int(last) >= cooldown_days

    def _eligible_templates(self, game_day: int) -> List[str]:
        out: List[str] = []
        for tid, tpl in self.defs.items():
            if game_day < int(tpl.get("min_day", 1)):
                continue
            if not self._cooldown_ok(tid, game_day, int(tpl.get("cooldown_days", 0))):
                continue
            out.append(tid)
        return out

    def maybe_spawn(self, game_day: int, game_hour: int = 8) -> Optional[Dict[str, Any]]:
        """若当前无 active 危机，按权重抽一个可用模板并落库。返回视图或 None。"""
        if self.get_active() is not None:
            return None
        eligible = self._eligible_templates(game_day)
        if not eligible:
            return None
        weights = [float(self.defs[t].get("weight", 1)) for t in eligible]
        tid = random.choices(eligible, weights=weights, k=1)[0]
        return self._insert(tid, game_day, game_hour)

    def force_spawn(self, game_day: int, template_id: str = "", game_hour: int = 8) -> Optional[Dict[str, Any]]:
        """调试：立即触发一个危机（忽略 min_day/cooldown）。已有 active 则先返回它。"""
        active = self.get_active()
        if active is not None:
            return active
        if template_id and template_id in self.defs:
            tid = template_id
        elif self.defs:
            tid = random.choice(list(self.defs.keys()))
        else:
            return None
        return self._insert(tid, game_day, game_hour)

    def _insert(self, template_id: str, game_day: int, game_hour: int = 8) -> Dict[str, Any]:
        deadline = game_day * 24 + int(game_hour) + DEADLINE_GAME_HOURS
        with get_conn() as conn:
            cur = conn.execute(
                """INSERT INTO crisis_state (template_id, game_day, status, deadline_hour, created_at)
                   VALUES (?, ?, 'active', ?, ?)""",
                (template_id, game_day, deadline, int(time.time())),
            )
            cid = cur.lastrowid or 0
        log.info("[crisis] 触发危机 id=%d template=%s day=%d 截止绝对时=%d",
                 cid, template_id, game_day, deadline)
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM crisis_state WHERE crisis_id = ?", (cid,)
            ).fetchone()
        return self._row_to_view(dict(row))

    # ---- 软性截止：超时自动「不管」结算 ----

    def _ignore_option(self, tpl: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        opts = tpl.get("options", [])
        for o in opts:
            if o.get("id") == "ignore" or "失职" in str(o.get("tag", "")):
                return o
        return opts[-1] if opts else None

    async def check_expired(self, game_day: int, game_hour: int) -> Optional[Dict[str, Any]]:
        """当前 active 危机若已过软性截止，自动按「不管」结算并返回结果（含 expired=True）。"""
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM crisis_state WHERE status = 'active' ORDER BY crisis_id DESC LIMIT 1"
            ).fetchone()
        if not row:
            return None
        row = dict(row)
        dh = row.get("deadline_hour")
        if dh is None:
            return None
        now_abs = game_day * 24 + int(game_hour)
        if now_abs < int(dh):
            return None
        tpl = self.defs.get(row["template_id"], {})
        opt = self._ignore_option(tpl)
        if not opt:
            return None
        res = await self.resolve(int(row["crisis_id"]), opt.get("id", ""), {})
        if res.get("ok"):
            res["expired"] = True
            log.info("[crisis] 超时自动结算 id=%d now=%d deadline=%d",
                     row["crisis_id"], now_abs, int(dh))
        return res

    # ---- 展示（拉双方说法）----

    async def open_view(self, crisis_id: int) -> Optional[Dict[str, Any]]:
        """打开调解面板：确保双方说法已生成（缓存），返回完整视图。"""
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM crisis_state WHERE crisis_id = ?", (crisis_id,)
            ).fetchone()
        if not row:
            return None
        row = dict(row)
        view = self._row_to_view(row)
        if view["statements"]:
            return view

        tpl = self.defs.get(row["template_id"], {})
        statements: Dict[str, str] = {}
        for p in tpl.get("parties", []):
            statements[p] = await self._gen_statement(p, tpl)
        with get_conn() as conn:
            conn.execute(
                "UPDATE crisis_state SET statements_json = ? WHERE crisis_id = ?",
                (json.dumps(statements, ensure_ascii=False), crisis_id),
            )
        view["statements"] = statements
        return view

    async def _gen_statement(self, npc_id: str, tpl: Dict[str, Any]) -> str:
        persona = self.personas.get(npc_id, {})
        name = persona.get("name", npc_id)
        title = self._player_title()
        who = f"{title}（玩家）" if title == "镇长" else f"正在竞选镇长的{title}（玩家）"
        fallback = f"（{name}气呼呼地把事情又说了一遍，等着你评理。）"
        if self.llm is None:
            return fallback
        sys_prompt = (
            f"你扮演 {name}，{persona.get('species','怪物')}。"
            f"性格：{persona.get('personality','')}\n"
            f"说话风格：{persona.get('speech_style','')}\n"
            f"镇上出了件纠纷，{who}来给大家评理。事情经过：{tpl.get('summary','')}\n"
            f"注意：对方目前是{title}，若还不是镇长就别称呼「镇长」。\n"
            f"现在你站在自己的立场，向对方陈述你这一方的说法与委屈，"
            f"用一句话（不超过 35 字），符合你的性格。直接说话，不要旁白。"
        )
        try:
            resp = await self.llm.chat(
                messages=[{"role": "system", "content": sys_prompt}],
                max_tokens=90,
                temperature=0.9,
            )
            line = resp.strip().strip("「」\"'")
            if line:
                return line[:90]
        except Exception as e:
            log.warning("[crisis] 生成 %s 说法失败: %s", npc_id, e)
        return fallback

    # ---- 调解结算 ----

    async def resolve(
        self, crisis_id: int, option_id: str, inventory: Dict[str, int]
    ) -> Dict[str, Any]:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM crisis_state WHERE crisis_id = ? AND status = 'active'",
                (crisis_id,),
            ).fetchone()
        if not row:
            return {"ok": False, "error": "危机已处理或不存在"}
        row = dict(row)
        tpl = self.defs.get(row["template_id"], {})
        option = next((o for o in tpl.get("options", []) if o.get("id") == option_id), None)
        if option is None:
            return {"ok": False, "error": "无效的调解方案"}

        # 校验道具
        req = option.get("requires", {}) or {}
        consume = {}
        if req.get("item"):
            need = int(req.get("count", 1))
            have = int((inventory or {}).get(req["item"], 0))
            if have < need:
                iname = req["item"]
                return {"ok": False, "error": f"你身上的 {iname} 不够（需 {need} 个）"}
            consume = {"item": req["item"], "count": need}

        title = tpl.get("title", row["template_id"])
        tag = option.get("tag", "")
        effects: Dict[str, int] = option.get("effects", {}) or {}

        # 应用好感 + 写 world_event（喂选举 event 子项）+ 写记忆
        affected: List[Dict[str, Any]] = []
        for npc_id, delta in effects.items():
            delta = int(delta)
            snap = self.affection.adjust(npc_id, delta)
            affected.append({
                "npc_id": npc_id,
                "delta": int(snap.get("delta", 0)),
                "affection": int(snap.get("value", 0)),
                "level": str(snap.get("level", "neutral")),
            })
            self._write_event(npc_id, delta, title)
            self._write_memory(npc_id, tag, title)

        # 生成双方对裁决的反应台词
        reactions = await self._gen_reactions(tpl, option)

        with get_conn() as conn:
            conn.execute(
                """UPDATE crisis_state SET status = 'resolved', chosen_option = ?, resolved_at = ?
                   WHERE crisis_id = ?""",
                (option_id, int(time.time()), crisis_id),
            )
        log.info("[crisis] 结算 id=%d option=%s effects=%s", crisis_id, option_id, effects)

        return {
            "ok": True,
            "crisis_id": crisis_id,
            "title": title,
            "tag": tag,
            "reactions": reactions,     # {npc_id: 反应台词}
            "affected": affected,
            "consume": consume,
        }

    def _write_event(self, npc_id: str, delta: int, title: str) -> None:
        """写 world_event。含 npc_id + 玩家 + 情感词，供 election._calc_event 命中。"""
        role = self._player_title()
        if delta > 0:
            desc = f"玩家（{role}）公正处理了「{title}」，帮 {npc_id} 解决了纠纷，{npc_id} 很满意。"
        elif delta < 0:
            desc = f"玩家（{role}）处理「{title}」时没向着 {npc_id}，{npc_id} 觉得受了委屈、很麻烦。"
        else:
            return
        try:
            self.world.add(actor="player", description=desc)
        except Exception as e:
            log.warning("[crisis] 写事件失败: %s", e)

    def _write_memory(self, npc_id: str, tag: str, title: str) -> None:
        if self.memory is None:
            return
        role = self._player_title()
        try:
            self.memory.add(
                npc_id,
                f"{role}{('，'+tag) if tag else ''}地处理了牵涉到我的「{title}」。",
                type="event",
                speaker="system",
                importance=6,
            )
        except Exception as e:
            log.warning("[crisis] 写记忆失败: %s", e)

    async def _gen_reactions(
        self, tpl: Dict[str, Any], option: Dict[str, Any]
    ) -> Dict[str, str]:
        effects: Dict[str, int] = option.get("effects", {}) or {}
        out: Dict[str, str] = {}
        for npc_id in tpl.get("parties", []):
            delta = int(effects.get(npc_id, 0))
            out[npc_id] = await self._gen_reaction(npc_id, tpl, option, delta)
        return out

    async def _gen_reaction(
        self, npc_id: str, tpl: Dict[str, Any], option: Dict[str, Any], delta: int
    ) -> str:
        persona = self.personas.get(npc_id, {})
        name = persona.get("name", npc_id)
        title = self._player_title()
        if delta > 0:
            mood, fb = "满意、感激", f"（{name}露出满意的神色，向{title}道谢。）"
        elif delta < 0:
            mood, fb = "不满、憋屈但不敢公然顶撞", f"（{name}撇撇嘴，勉强接受了裁决。）"
        else:
            mood, fb = "平静接受", f"（{name}点点头，没多说什么。）"
        if self.llm is None:
            return fb
        sys_prompt = (
            f"你扮演 {name}，{persona.get('species','怪物')}。"
            f"性格：{persona.get('personality','')}\n"
            f"说话风格：{persona.get('speech_style','')}\n"
            f"{title}（玩家）刚就纠纷「{tpl.get('title','')}」做出裁决：{option.get('label','')}\n"
            f"注意：对方目前是{title}，若还不是镇长就别称呼「镇长」。\n"
            f"这个结果对你而言你感到{mood}。\n"
            f"用一句话（不超过 30 字）说出你此刻的反应，符合性格。直接说话，不要旁白。"
        )
        try:
            resp = await self.llm.chat(
                messages=[{"role": "system", "content": sys_prompt}],
                max_tokens=80,
                temperature=0.9,
            )
            line = resp.strip().strip("「」\"'")
            if line:
                return line[:80]
        except Exception as e:
            log.warning("[crisis] 生成 %s 反应失败: %s", npc_id, e)
        return fb
