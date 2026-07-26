"""对手 NPC 每日动作 AI。

设计参考 docs/mayor_loop.md §4。

D3 范围（本次 commit）：
- generate_platform(term)：每个新任期为对手 LLM 生成"竞选纲领"，写入 candidate_state.platform_json
- run_daily_action(term, game_day)：每日 07:00 调用，生成 1 个 visit 动作（最简版）
- 写 opponent_actions 表 + world_events
- visit 机械效果：目标 NPC 对对手 base_loyalty 倾斜（通过写一条积极 world_event 间接影响 event 子项）

后续 D9+ 扩展：smear / promise / speech 三种额外动作 + 反偷听 / 联合行动等连任阶梯
"""
from __future__ import annotations

import json
import logging
import random
import time
from typing import Any, Dict, List, Optional, Tuple

from db import get_conn

log = logging.getLogger("opponent_ai")

PLAYER_ID = "player"

# 视野内可拜访的目标 NPC（不含玩家、对手自己）
ACTION_VISIT = "visit"
ACTION_PROMISE = "promise"
ACTION_SMEAR = "smear"

# 落后玩家超过此分 → 当日行动数 +1，且更倾向抹黑（追赶施压）
CATCHUP_BEHIND_THRESHOLD = 25.0

# 双向橡皮筋：对手领先玩家时收手（陪玩定位）
AHEAD_EASE_THRESHOLD = 10.0   # 对手领先超此分 → 收手，当日最多 1 个温和动作
AHEAD_STOP_THRESHOLD = 25.0   # 对手领先超此分 → 完全等一等，当日 0 动作

# 后端 LLM 失败时的兜底文案（不阻塞主流程）
FALLBACK_VISIT_TEXTS = [
    "我希望镇上能少一点喧闹，多一点踏实。",
    "你最近过得怎么样？有什么需要我帮忙的尽管说。",
    "等我当上镇长，第一件事就是来看看你。",
    "镇上的事我都记着，你的难处我也记着。",
]

FALLBACK_PLATFORM = [
    "守护我们镇子原本的样子，不要乱改。",
    "把每个居民的小事都当作大事来办。",
    "让镇上的孩子和老人都安心。",
]

FALLBACK_PROMISE_TEXTS = [
    "我答应你，当上镇长第一件事就帮你把这事办妥。",
    "你的难处我记下了，给我个机会，我一定兑现。",
    "选我，我保证让你往后的日子踏实不少。",
]

FALLBACK_SMEAR_TEXTS = [
    "你也看见了吧？那位新来的，嘴上一套背后一套，靠得住吗？",
    "我听说他答应别人的事，转头就忘——这样的人能当镇长？",
    "别被花言巧语骗了，他到现在连镇上的规矩都没摸清。",
]


class OpponentAI:
    """对手 NPC 行为生成器。"""

    def __init__(
        self,
        election_store,
        personas: Dict[str, Dict[str, Any]],
        llm,
        world_store,
        memory_store=None,
    ) -> None:
        self.election = election_store
        self.personas = personas
        self.llm = llm
        self.world = world_store
        self.memory = memory_store

    # ---- 立场 / 纲领 ----

    async def ensure_platform(self, term: Dict) -> List[str]:
        """若 candidate_state.platform_json 为空，调 LLM 生成；返回纲领条目列表。"""
        term_id = int(term["term_id"])
        opponent_id = term["opponent_id"]

        with get_conn() as conn:
            row = conn.execute(
                "SELECT platform_json FROM candidate_state WHERE term_id = ? AND candidate_id = ?",
                (term_id, opponent_id),
            ).fetchone()

        existing = None
        if row and row["platform_json"]:
            try:
                existing = json.loads(row["platform_json"])
            except Exception:
                existing = None
        if isinstance(existing, list) and existing:
            return existing

        platform = await self._generate_platform(opponent_id)
        with get_conn() as conn:
            conn.execute(
                """UPDATE candidate_state SET platform_json = ?
                   WHERE term_id = ? AND candidate_id = ?""",
                (json.dumps(platform, ensure_ascii=False), term_id, opponent_id),
            )
        log.info("[opponent_ai] term=%d 对手=%s 生成纲领: %s", term_id, opponent_id, platform)
        return platform

    async def _generate_platform(self, opponent_id: str) -> List[str]:
        persona = self.personas.get(opponent_id, {})
        name = persona.get("name", opponent_id)
        species = persona.get("species", "怪物")
        personality = persona.get("personality", "")
        facts = persona.get("important_facts", [])

        sys_prompt = (
            "你正在为一只森林小镇的居民撰写竞选镇长的纲领。\n"
            "结合该居民的性格与人物背景，写出 3 条具体、有立场的承诺，\n"
            "每条不超过 25 字，第一人称口吻。\n"
            "只输出 JSON 数组，例如：[\"承诺 1\", \"承诺 2\", \"承诺 3\"]"
        )
        user_prompt = (
            f"角色：{name}（{species}）\n"
            f"性格：{personality}\n"
            f"背景事实：\n- " + "\n- ".join(facts[:5])
        )
        try:
            resp = await self.llm.chat(
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=200,
                temperature=0.8,
            )
            data = json.loads(_extract_json_array(resp))
            if isinstance(data, list) and data:
                return [str(x).strip() for x in data[:5] if str(x).strip()]
        except Exception as e:
            log.warning("[opponent_ai] 生成纲领失败: %s", e)
        return list(FALLBACK_PLATFORM)

    # ---- 每日动作 ----

    def _daily_action_count(self, day_index: int, gap: float) -> int:
        """当日行动数（双向橡皮筋）。

        gap = 玩家分 − 对手分：>0 玩家领先，<0 对手领先。
        - 对手领先 > AHEAD_STOP_THRESHOLD → 0（完全等一等）
        - 对手领先 > AHEAD_EASE_THRESHOLD → 1（收手，温和拉票）
        - 接近 → 随任期推进 base 1~3
        - 玩家领先 > CATCHUP_BEHIND_THRESHOLD → base+1（追赶施压）
        """
        if gap < -AHEAD_STOP_THRESHOLD:
            return 0
        if gap < -AHEAD_EASE_THRESHOLD:
            return 1
        if day_index <= 1:
            base = 2
        else:
            base = 3
        if gap > CATCHUP_BEHIND_THRESHOLD:
            base += 1
        return min(base, 4)

    def _voter_targets(self, opponent_id: str) -> List[str]:
        return [n for n in self.election.npc_ids if n != opponent_id and n != PLAYER_ID]

    def _pick_targets_by_score(self, term: Dict, opponent_id: str, count: int) -> List[Tuple[str, str]]:
        """按当前每个 voter 的双方权重智能选目标 + 动作类型。

        - 玩家在该 voter 领先很多 → smear（夺票）
        - 双方接近的摇摆 voter → promise（争取）
        - 其余 → visit（常规拉票）
        返回 [(voter_id, action_type), ...]
        """
        voters = self._voter_targets(opponent_id)
        if not voters:
            return []
        # 计算每个 voter 玩家领先对手的分差
        leads: List[Tuple[str, float]] = []
        for v in voters:
            try:
                pw, _ = self.election.compute_weight(v, PLAYER_ID, term)
                ow, _ = self.election.compute_weight(v, opponent_id, term)
            except Exception:
                pw, ow = 0.0, 0.0
            leads.append((v, pw - ow))
        # 玩家领先越多越优先攻打
        leads.sort(key=lambda x: x[1], reverse=True)

        picks: List[Tuple[str, str]] = []
        for v, lead in leads[:count]:
            if lead >= 18.0:
                action = ACTION_SMEAR       # 玩家明显领先 → 抹黑夺票
            elif abs(lead) <= 8.0:
                action = ACTION_PROMISE     # 摇摆票 → 许诺争取
            else:
                action = ACTION_VISIT       # 常规拉票
            picks.append((v, action))
        return picks

    async def run_daily_actions(
        self,
        term: Dict,
        game_day: int,
        player_score: float = 0.0,
        opponent_score: float = 0.0,
        force: bool = False,
    ) -> List[Dict]:
        """生成对手的一批动作（强劲追赶）。

        行动数随任期推进 + 落后幅度增加；动作类型按比分智能选（visit/promise/smear）。
        force=False 时同一游戏日只执行一批（防重复）；
        force=True 跳过该守卫（用于定时多批 / 调试键即时触发）。
        返回动作 dict 列表（可能为空）。
        """
        term_id = int(term["term_id"])
        opponent_id = term["opponent_id"]

        # 当日是否已执行（force 跳过）
        if not force:
            with get_conn() as conn:
                row = conn.execute(
                    """SELECT COUNT(*) AS c FROM opponent_actions
                       WHERE term_id = ? AND game_day = ?""",
                    (term_id, game_day),
                ).fetchone()
            if row and int(row["c"]) > 0:
                return []  # 今日已生成过

        day_index = self.election.day_index_in_term(term, game_day)
        gap = player_score - opponent_score
        n_actions = self._daily_action_count(day_index, gap)
        if n_actions <= 0:
            log.info(
                "[opponent_ai] term=%d day=%d 分差=%.1f 对手领先收手，0 动作",
                term_id, game_day, gap,
            )
            return []

        platform = await self.ensure_platform(term)
        picks = self._pick_targets_by_score(term, opponent_id, n_actions)
        if not picks:
            return []

        actions: List[Dict] = []
        for target_id, action_type in picks:
            action = await self._execute_action(
                term_id, game_day, opponent_id, target_id, action_type, platform
            )
            if action:
                actions.append(action)
        log.info(
            "[opponent_ai] term=%d day=%d 分差=%.1f 行动数=%d 动作=%s",
            term_id, game_day, gap, len(actions),
            [(a["target_npc"], a["action_type"]) for a in actions],
        )
        return actions

    async def _execute_action(
        self,
        term_id: int,
        game_day: int,
        opponent_id: str,
        target_id: str,
        action_type: str,
        platform: List[str],
    ) -> Optional[Dict]:
        """执行单个对手动作：生台词 + 写 opponent_actions + 写 world_events。"""
        op_name = self.personas.get(opponent_id, {}).get("name", opponent_id)
        target_name = self.personas.get(target_id, {}).get("name", target_id)

        text = await self._generate_action_line(opponent_id, target_id, action_type, platform)

        effect = {"target": target_id, "kind": action_type, "magnitude": 1}
        with get_conn() as conn:
            conn.execute(
                """INSERT INTO opponent_actions
                   (term_id, game_day, candidate_id, action_type, target_npc, llm_text, mechanical_effect_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    term_id, game_day, opponent_id, action_type, target_id, text,
                    json.dumps(effect, ensure_ascii=False),
                    int(time.time()),
                ),
            )

        # 写 world_events（让 voter 的 event 子项可感知）
        if action_type == ACTION_SMEAR:
            desc = f"{op_name} 在 {target_name} 面前数落玩家的不是：「{text[:30]}」"
        elif action_type == ACTION_PROMISE:
            desc = f"{op_name} 向 {target_name} 许诺帮忙：「{text[:30]}」"
        else:
            desc = f"{op_name} 拜访 {target_name}，承诺 帮助 {target_name}：「{text[:30]}」"
        self.world.add(actor=opponent_id, description=desc)

        return {
            "term_id": term_id,
            "game_day": game_day,
            "candidate_id": opponent_id,
            "action_type": action_type,
            "target_npc": target_id,
            "llm_text": text,
            "mechanical_effect": effect,
        }

    async def run_daily_action(
        self,
        term: Dict,
        game_day: int,
    ) -> Optional[Dict]:
        """兼容旧接口：调多动作版，返回首个动作（或 None）。"""
        actions = await self.run_daily_actions(term, game_day)
        return actions[0] if actions else None

    async def _generate_action_line(
        self,
        opponent_id: str,
        target_id: str,
        action_type: str,
        platform: List[str],
    ) -> str:
        """按动作类型生成台词。visit/promise 正面，smear 抹黑玩家。"""
        op_persona = self.personas.get(opponent_id, {})
        target_persona = self.personas.get(target_id, {})
        op_name = op_persona.get("name", opponent_id)
        target_name = target_persona.get("name", target_id)
        common = (
            f"你扮演 {op_name}，{op_persona.get('species','怪物')}。"
            f"性格：{op_persona.get('personality','')}\n"
            f"说话风格：{op_persona.get('speech_style','')}\n"
            f"你正在竞选镇长。你的纲领：{platform}\n"
        )
        if action_type == ACTION_SMEAR:
            sys_prompt = common + (
                f"现在你私下对居民 {target_name} 说玩家（你的竞选对手）的坏话，"
                f"想让 {target_name} 别投玩家。\n"
                f"用一句话（不超过 30 字）含蓄地抹黑对手，不要太露骨。直接说话，不要旁白。"
            )
            fallback = FALLBACK_SMEAR_TEXTS
        elif action_type == ACTION_PROMISE:
            sys_prompt = common + (
                f"现在你向摇摆中的居民 {target_name} 许下一个具体承诺，争取他/她的票。\n"
                f"用一句话（不超过 30 字）给出诚恳的承诺。直接说话，不要旁白。"
            )
            fallback = FALLBACK_PROMISE_TEXTS
        else:
            sys_prompt = common + (
                f"现在你拜访居民 {target_name}，想拉拢他/她支持你。\n"
                f"用一句话（不超过 30 字）打动对方。直接说话，不要旁白。"
            )
            fallback = FALLBACK_VISIT_TEXTS
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
            log.warning("[opponent_ai] 生成 %s 台词失败: %s", action_type, e)
        return random.choice(fallback)

    async def _generate_visit_line(
        self,
        opponent_id: str,
        target_id: str,
        platform: List[str],
    ) -> str:
        op_persona = self.personas.get(opponent_id, {})
        target_persona = self.personas.get(target_id, {})
        op_name = op_persona.get("name", opponent_id)
        target_name = target_persona.get("name", target_id)

        sys_prompt = (
            f"你扮演 {op_name}，{op_persona.get('species','怪物')}。"
            f"性格：{op_persona.get('personality','')}\n"
            f"说话风格：{op_persona.get('speech_style','')}\n"
            f"你正在竞选镇长。你的纲领：{platform}\n"
            f"现在你拜访居民 {target_name}，想拉拢他/她支持你。\n"
            f"用一句话（不超过 30 字）打动对方。直接说话，不要旁白。"
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
            log.warning("[opponent_ai] 生成 visit 台词失败: %s", e)
        return random.choice(FALLBACK_VISIT_TEXTS)

    # ---- 查询接口 ----

    def list_today_actions(self, term_id: int, game_day: int) -> List[Dict]:
        with get_conn() as conn:
            rows = conn.execute(
                """SELECT * FROM opponent_actions
                   WHERE term_id = ? AND game_day = ?
                   ORDER BY action_id ASC""",
                (term_id, game_day),
            ).fetchall()
        return [dict(r) for r in rows]


def _extract_json_array(text: str) -> str:
    """从 LLM 输出中抽出第一个 JSON 数组。容错。"""
    if not text:
        return "[]"
    s = text.strip()
    # 去掉 ```json ``` markdown 包裹
    if s.startswith("```"):
        s = s.strip("`")
        s = s.replace("json\n", "", 1).replace("json", "", 1)
    # 找第一个 [ 到对应 ]
    start = s.find("[")
    end = s.rfind("]")
    if start >= 0 and end > start:
        return s[start:end + 1]
    return "[]"
