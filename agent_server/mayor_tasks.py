"""镇长政务任务（现任镇长专属玩法）。

循环：随机刷新政务任务 → 玩家在某 NPC 对话里「安排 TA 去做」并选说服方式
（好感说服 / 讲道理 / 威胁）→ NPC 接受则寻路到现场表演 → 按
人岗匹配(fit) + 方式 + 心情/好感 结算 搞砸/马马虎虎/漂亮。

满意度即选举分：结果写 world_events（actor=player → 全镇 event 子项），
安排方式影响被指派者个人 affection（威胁掉好感）。不额外建满意度表。

战斗类（制服酒鬼）：比拼战斗力（= 制服排序的名次），打不过 → 搞砸且执行者受伤。
"""
from __future__ import annotations

import logging
import random
import time
from typing import Any, Dict, List, Optional

from db import get_conn

log = logging.getLogger("mayor_tasks")

# NPC 简称对照（设计图）：苔=bear_baker 咸=pirate_lao 红=mystic_xuan
# 焰=fox_postman 蓝=traveler_lan 翠=herbalist_cui  你=player

TASK_DEFS: Dict[str, Dict[str, Any]] = {
    "subdue_drunk": {
        "title": "制服酒鬼",
        "kind": "combat",
        "needs_drunk": True,
        # 制服排序 = 全局战斗力排名（越靠前越能打）
        "fit_order": ["bear_baker", "pirate_lao", "mystic_xuan",
                      "fox_postman", "player", "traveler_lan", "herbalist_cui"],
        "hint": "得找镇上最能打的硬手，文弱的去了会挨揍。",
    },
    "repair_sewer": {
        "title": "修理下水道",
        "kind": "skill",
        "needs_drunk": False,
        "fit_order": ["pirate_lao", "traveler_lan"],
        "hint": "得找懂管道、肯下脏活的人。",
    },
    "cure_epidemic": {
        "title": "治疗传染病",
        "kind": "skill",
        "needs_drunk": False,
        "needs_sick": True,   # 随机指定一名病人 NPC（执行者需赶到其身边治疗）
        "fit_order": ["herbalist_cui", "mystic_xuan"],
        "hint": "得找懂医术药理的人。",
    },
    "clean": {
        "title": "打扫卫生",
        "kind": "skill",
        "needs_drunk": False,
        "fit_order": ["fox_postman", "traveler_lan"],
        "hint": "得找勤快麻利的人。",
    },
    "archive": {
        "title": "整理档案",
        "kind": "skill",
        "needs_drunk": False,
        "fit_order": ["mystic_xuan", "traveler_lan"],
        "hint": "得找细心、识文断字的人。",
    },
}

# 刷新节奏（用游戏时间，兼容加速时钟）
DAILY_CAP = 3               # 每天最多刷几个
COOLDOWN_HOURS = 4          # 上一个结算后，间隔多少游戏小时再刷下一个
AWAKE_START, AWAKE_END = 8, 20

# 结算/效果参数
EVENT_POSITIVE = "圆满解决"   # 含选举 POSITIVE_WORDS「解决」
EVENT_NEGATIVE = "闹出麻烦"   # 含选举 NEGATIVE_WORDS「麻烦」
THREAT_AFF = -6              # 威胁掉好感
PERSUADE_AFF = 2            # 好感说服（成功）小幅加好感
GREAT_MOOD, BOTCH_MOOD = 12, -10
INJURED_AFF, INJURED_MOOD = -4, -18

FALLBACK_ACCEPT = "行，这事交给我。"
FALLBACK_REFUSE = "这活儿……我可不干。"
FALLBACK_RESULT = {
    "great": "干得漂亮，事情办得妥妥的。",
    "ok": "马马虎虎办完了，凑合。",
    "botch": "唉，搞砸了，越帮越忙。",
}


class MayorTaskManager:
    def __init__(self, election_store, affection_store, mood_store,
                 world_store, personas: Dict[str, Dict[str, Any]],
                 npc_ids: List[str], llm=None) -> None:
        self.election = election_store
        self.affection = affection_store
        self.mood = mood_store
        self.world = world_store
        self.personas = personas
        self.npc_ids = list(npc_ids)
        self.llm = llm

    # ---- 基础 ----

    def _name(self, npc_id: str) -> str:
        if npc_id == "player":
            return "你"
        return self.personas.get(npc_id, {}).get("name", npc_id)

    def get_active(self) -> Optional[Dict[str, Any]]:
        with get_conn() as conn:
            row = conn.execute(
                """SELECT * FROM mayor_task WHERE status IN ('open','assigned')
                   ORDER BY id DESC LIMIT 1"""
            ).fetchone()
        return dict(row) if row else None

    def _count_today(self, game_day: int) -> int:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM mayor_task WHERE spawn_day = ?",
                (game_day,),
            ).fetchone()
        return int(row["c"]) if row else 0

    def _last_resolved_abshour(self) -> int:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT resolved_at FROM mayor_task WHERE status='resolved' "
                "ORDER BY resolved_at DESC LIMIT 1"
            ).fetchone()
        # resolved_at 存的是绝对游戏小时(day*24+hour)
        return int(row["resolved_at"]) if row and row["resolved_at"] is not None else -999

    # ---- 刷新 ----

    def maybe_spawn(self, game_day: int, game_hour: int) -> Optional[Dict[str, Any]]:
        """满足节流条件则刷一个新任务，返回视图；否则 None。仅现任镇长时调用。"""
        if not (AWAKE_START <= game_hour <= AWAKE_END):
            return None
        if self.get_active() is not None:
            return None
        if self._count_today(game_day) >= DAILY_CAP:
            return None
        abs_hour = game_day * 24 + game_hour
        if abs_hour - self._last_resolved_abshour() < COOLDOWN_HOURS:
            return None

        task_type = random.choice(list(TASK_DEFS.keys()))
        tdef = TASK_DEFS[task_type]
        target_id = ""
        if tdef.get("needs_drunk") or tdef.get("needs_sick"):
            # 随机一个 NPC 变成酒鬼/病人（执行者需赶到其身边）
            cands = [n for n in self.npc_ids if n != "player"]
            if not cands:
                return None
            target_id = random.choice(cands)

        with get_conn() as conn:
            cur = conn.execute(
                """INSERT INTO mayor_task
                   (task_type, status, target_id, spawn_day, created_at)
                   VALUES (?, 'open', ?, ?, ?)""",
                (task_type, target_id, game_day, int(time.time())),
            )
            task_id = cur.lastrowid or 0
        log.info("[mayor_task] spawn id=%d type=%s target=%s day=%d",
                 task_id, task_type, target_id, game_day)
        return self.view()

    def force_spawn(self, game_day: int,
                    task_type: str = "") -> Optional[Dict[str, Any]]:
        """调试：忽略节流强制刷一个（仍要求当前无进行中任务）。"""
        if self.get_active() is not None:
            return self.view()
        if task_type not in TASK_DEFS:
            task_type = random.choice(list(TASK_DEFS.keys()))
        tdef = TASK_DEFS[task_type]
        target_id = ""
        if tdef.get("needs_drunk") or tdef.get("needs_sick"):
            cands = [n for n in self.npc_ids if n != "player"]
            if not cands:
                return None
            target_id = random.choice(cands)
        with get_conn() as conn:
            conn.execute(
                """INSERT INTO mayor_task
                   (task_type, status, target_id, spawn_day, created_at)
                   VALUES (?, 'open', ?, ?, ?)""",
                (task_type, target_id, game_day, int(time.time())),
            )
        log.info("[mayor_task] force_spawn type=%s target=%s day=%d",
                 task_type, target_id, game_day)
        return self.view()


    # ---- 视图 ----

    def view(self) -> Optional[Dict[str, Any]]:
        task = self.get_active()
        if task is None:
            return None
        tdef = TASK_DEFS.get(task["task_type"], {})
        target_id = task["target_id"] or ""
        return {
            "id": int(task["id"]),
            "task_type": task["task_type"],
            "title": tdef.get("title", task["task_type"]),
            "kind": tdef.get("kind", "skill"),
            "status": task["status"],
            "hint": tdef.get("hint", ""),
            "target_id": target_id,
            "target_name": self._name(target_id) if target_id else "",
        }

    def eligible_executor(self, task: Dict[str, Any], npc_id: str) -> bool:
        """该 NPC 现在能否被指派：不能是酒鬼/病人本人。"""
        if npc_id == "player":
            return False
        tdef = TASK_DEFS.get(task["task_type"], {})
        # 酒鬼/病人本人不能被派去处置自己
        if (tdef.get("needs_drunk") or tdef.get("needs_sick")) \
                and npc_id == (task["target_id"] or ""):
            return False
        return npc_id in self.npc_ids

    # ---- 结算辅助 ----

    def _fit_rank(self, task_type: str, npc_id: str) -> int:
        order = TASK_DEFS[task_type].get("fit_order", [])
        return order.index(npc_id) if npc_id in order else len(order) + 2

    def decide_accept(self, task: Dict[str, Any], executor_id: str, method: str) -> bool:
        if method == "threat":
            return True
        rank = self._fit_rank(task["task_type"], executor_id)
        aff = self.affection.get(executor_id) if self.affection else 0
        if method == "persuade":
            p = 0.35 + aff / 120.0
        else:  # reason
            p = 0.55
        p += max(0, 2 - rank) * 0.08
        try:
            mv = self.mood.get(executor_id) if self.mood else 0
        except Exception:
            mv = 0
        p += mv / 300.0
        p = max(0.05, min(0.97, p))
        return random.random() < p

    def _decide_outcome(self, task: Dict[str, Any], executor_id: str,
                        method: str) -> Dict[str, Any]:
        """返回 {outcome: great|ok|botch, injured: bool}。"""
        task_type = task["task_type"]
        injured = False

        if TASK_DEFS[task_type].get("kind") == "combat":
            drunk = task["target_id"] or ""
            ew = self._fit_rank(task_type, executor_id)   # 越小越能打
            dw = self._fit_rank(task_type, drunk)
            diff = dw - ew  # >0 执行者更强
            if diff <= 0:
                # 打不过 → 搞砸 + 受伤
                return {"outcome": "botch", "injured": True}
            if diff >= 2:
                base = "great"
            else:
                base = "ok"
            # 威胁去打（不情愿）稍降档
            if method == "threat" and base == "great" and random.random() < 0.4:
                base = "ok"
            return {"outcome": base, "injured": injured}

        # 技能类：连续质量分
        rank = self._fit_rank(task_type, executor_id)
        quality = 3.0 - rank * 1.2
        if method == "threat":
            quality -= 1.0
        elif method == "persuade":
            quality += 0.3
        try:
            quality += (self.affection.get(executor_id) - 30) / 100.0 if self.affection else 0
        except Exception:
            pass
        try:
            quality += (self.mood.get(executor_id) if self.mood else 0) / 100.0
        except Exception:
            pass
        quality += random.uniform(-0.6, 0.6)
        if quality >= 2.0:
            outcome = "great"
        elif quality >= 0.8:
            outcome = "ok"
        else:
            outcome = "botch"
        return {"outcome": outcome, "injured": injured}

    # ---- 分配 + 结算（一次完成，客户端负责表演回放）----

    async def assign(self, task_id: int, executor_id: str, method: str,
                     game_day: int, game_hour: int) -> Dict[str, Any]:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM mayor_task WHERE id = ?", (task_id,)
            ).fetchone()
        if row is None:
            return {"ok": False, "executor_id": executor_id, "error": "任务不存在"}
        task = dict(row)
        if task["status"] != "open":
            return {"ok": False, "executor_id": executor_id, "error": "任务已被安排或已结束"}
        if method not in ("persuade", "reason", "threat"):
            return {"ok": False, "executor_id": executor_id, "error": "无效的说服方式"}
        if not self.eligible_executor(task, executor_id):
            return {"ok": False, "executor_id": executor_id, "error": "此人无法处理这件事"}

        accepted = self.decide_accept(task, executor_id, method)
        if not accepted:
            line = await self._gen_line(task, executor_id, method, "refuse")
            return {
                "ok": True, "accepted": False, "task_id": task_id,
                "executor_id": executor_id, "line": line,
            }

        res = self._decide_outcome(task, executor_id, method)
        outcome = res["outcome"]
        injured = res["injured"]
        effects = self._apply_effects(task, executor_id, method, outcome, injured)

        abs_hour = game_day * 24 + game_hour
        with get_conn() as conn:
            conn.execute(
                """UPDATE mayor_task SET status='resolved', executor_id=?, method=?,
                   outcome=?, resolved_at=? WHERE id=?""",
                (executor_id, method, outcome, abs_hour, task_id),
            )
        accept_line = await self._gen_line(task, executor_id, method, "accept")
        result_line = await self._gen_line(task, executor_id, method, outcome)
        log.info("[mayor_task] resolve id=%d exec=%s method=%s outcome=%s injured=%s",
                 task_id, executor_id, method, outcome, injured)
        return {
            "ok": True, "accepted": True, "task_id": task_id,
            "task_type": task["task_type"], "target_id": task["target_id"] or "",
            "executor_id": executor_id, "method": method,
            "outcome": outcome, "injured": injured,
            "accept_line": accept_line, "result_line": result_line,
            "effects": effects,
        }

    def _apply_effects(self, task: Dict[str, Any], executor_id: str,
                       method: str, outcome: str, injured: bool) -> Dict[str, Any]:
        tdef = TASK_DEFS[task["task_type"]]
        title = tdef.get("title", task["task_type"])
        ename = self._name(executor_id)

        # 全镇效果：写 world_event（actor=player → 所有 voter 的 event 子项）
        if self.world is not None:
            if outcome == "great":
                desc = ("玩家以镇长身份安排 %s 处理「%s」，%s，镇长赢得称赞。"
                        % (ename, title, EVENT_POSITIVE))
                self.world.add(actor="player", description=desc)
            elif outcome == "botch":
                desc = ("玩家以镇长身份安排 %s 处理「%s」，结果%s，镇长被埋怨用人不当。"
                        % (ename, title, EVENT_NEGATIVE))
                self.world.add(actor="player", description=desc)
            else:
                self.world.add(actor="player",
                               description="玩家安排 %s 处理了「%s」，勉强了事。" % (ename, title))

        # 个人效果：安排方式 + 结果 → 执行者 affection / mood
        aff_delta = 0
        mood_delta = 0
        if method == "threat":
            aff_delta += THREAT_AFF
            mood_delta -= 8
        elif method == "persuade":
            aff_delta += PERSUADE_AFF
        if outcome == "great":
            mood_delta += GREAT_MOOD
        elif outcome == "botch":
            mood_delta += BOTCH_MOOD
        if injured:
            aff_delta += INJURED_AFF
            mood_delta += INJURED_MOOD

        aff_after = None
        if self.affection is not None and aff_delta != 0:
            aff_after = self.affection.adjust(executor_id, aff_delta)
        if self.mood is not None and mood_delta != 0:
            try:
                self.mood.adjust(executor_id, mood_delta)
            except Exception:
                pass

        return {
            "executor_id": executor_id,
            "executor_name": ename,
            "aff_delta": aff_delta,
            "affection": int(aff_after.get("value", 0)) if aff_after else None,
            "level": aff_after.get("level") if aff_after else None,
            "mood_delta": mood_delta,
            "injured": injured,
        }

    # ---- LLM 台词 ----

    async def _gen_line(self, task: Dict[str, Any], executor_id: str,
                        method: str, phase: str) -> str:
        """phase: accept|refuse|great|ok|botch。"""
        fallback = (FALLBACK_ACCEPT if phase == "accept"
                    else FALLBACK_REFUSE if phase == "refuse"
                    else FALLBACK_RESULT.get(phase, ""))
        if self.llm is None:
            return fallback
        persona = self.personas.get(executor_id, {})
        title = TASK_DEFS[task["task_type"]].get("title", task["task_type"])
        method_cn = {"persuade": "用好感软磨", "reason": "讲道理", "threat": "威胁施压"}[method]
        if phase == "accept":
            ask = "镇长%s让 TA 去「%s」，TA 勉强/爽快答应，说一句：" % (method_cn, title)
        elif phase == "refuse":
            ask = "镇长%s让 TA 去「%s」，TA 不情愿地拒绝，说一句：" % (method_cn, title)
        else:
            res_cn = {"great": "干得漂亮", "ok": "马马虎虎完成", "botch": "彻底搞砸了"}[phase]
            ask = "TA 去「%s」的结果是%s，回来跟镇长汇报，说一句：" % (title, res_cn)
        sys = ("你替一位小镇居民生成一句符合其性格的短台词，不超过 28 字，"
               "口语、直接说话、不要旁白和引号。")
        user = "居民：%s（%s）。%s" % (
            persona.get("name", executor_id), persona.get("personality", ""), ask)
        try:
            resp = await self.llm.chat(
                messages=[{"role": "system", "content": sys},
                          {"role": "user", "content": user}],
                max_tokens=60, temperature=0.9,
            )
            line = (resp or "").strip().strip("「」\"'")
            if line:
                return line[:60]
        except Exception as e:
            log.warning("[mayor_task] LLM 台词失败 phase=%s: %s", phase, e)
        return fallback
