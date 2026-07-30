"""八卦系统（活社会核心）。

概念：
  - rumor         = 小镇流传的一个话题（主角 subject / 情感 sentiment / 真伪 truth /
                    热度 heat / 内容 content）。可由玩家显著言行自动生成，或玩家主动放话。
  - rumor_knowledge = 每只 NPC 知道哪条话题 + ta 口中的说法（版本）。每传一手可能被
                    LLM 按性格+心情改写（变味）。

流转闭环：
  玩家做事 → 生成话题(RumorManager.generate) → 见证的 NPC 成为初始知情者
        → NPC 相遇闲聊时把热门话题带出来(pick_gossip_for)，听者学到并变味(learn)
        → 话题传到当事人耳朵 → 当事人对玩家好感/印象变化(consequence，在 main 里处理)
        → 每日热度衰减，冷了就 faded。

存储全在 SQLite（rumor / rumor_knowledge 两表，见 db.py）。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from db import get_conn
from llm import LLMClient

log = logging.getLogger("rumor")

HEAT_MAX = 100
HEAT_SPREAD_GAIN = 6      # 每成功传一手，话题热度 +
HEAT_DECAY_PER_DAY = 8    # 每游戏日衰减
TELL_LIMIT = 2            # 单个 NPC 对同一话题最多主动传几次
SPREAD_MIN_HEAT = 12      # 低于此热度不再主动扩散


def sentiment_label(s: str) -> str:
    return {"praise": "夸赞", "smear": "抹黑", "neutral": "闲话"}.get(s, "闲话")


@dataclass
class Rumor:
    id: int
    subject_id: str
    sentiment: str
    truth: int
    heat: int
    content: str
    origin: str
    game_day: int
    status: str

    @classmethod
    def from_row(cls, r) -> "Rumor":
        return cls(
            id=r["id"], subject_id=r["subject_id"], sentiment=r["sentiment"],
            truth=int(r["truth"]), heat=int(r["heat"]), content=r["content"],
            origin=r["origin"] or "", game_day=int(r["game_day"]), status=r["status"],
        )


class RumorStore:
    """rumor / rumor_knowledge 两表的 CRUD。"""

    # ---------- rumor ----------

    def create(
        self,
        subject_id: str,
        content: str,
        *,
        sentiment: str = "neutral",
        truth: int = 1,
        heat: int = 45,
        origin: str = "auto",
        game_day: int = 0,
    ) -> int:
        now = int(time.time())
        with get_conn() as conn:
            cur = conn.execute(
                """INSERT INTO rumor
                   (subject_id, sentiment, truth, heat, content, origin, game_day,
                    status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
                (subject_id, sentiment, truth, heat, content, origin, game_day, now, now),
            )
            return cur.lastrowid or 0

    def get(self, rumor_id: int) -> Optional[Rumor]:
        with get_conn() as conn:
            row = conn.execute("SELECT * FROM rumor WHERE id = ?", (rumor_id,)).fetchone()
        return Rumor.from_row(row) if row else None

    def active_about(self, subject_id: str) -> List[Rumor]:
        with get_conn() as conn:
            rows = conn.execute(
                """SELECT * FROM rumor
                   WHERE subject_id = ? AND status = 'active'
                   ORDER BY heat DESC""",
                (subject_id,),
            ).fetchall()
        return [Rumor.from_row(r) for r in rows]

    def similar_today(self, subject_id: str, sentiment: str, game_day: int) -> Optional[Rumor]:
        """同主角同情感当日是否已有话题（自动生成去重用）。"""
        with get_conn() as conn:
            row = conn.execute(
                """SELECT * FROM rumor
                   WHERE subject_id = ? AND sentiment = ? AND game_day = ?
                     AND status = 'active' ORDER BY id DESC LIMIT 1""",
                (subject_id, sentiment, game_day),
            ).fetchone()
        return Rumor.from_row(row) if row else None

    def adjust_heat(self, rumor_id: int, delta: int) -> int:
        with get_conn() as conn:
            row = conn.execute("SELECT heat FROM rumor WHERE id = ?", (rumor_id,)).fetchone()
            if not row:
                return 0
            new_h = max(0, min(HEAT_MAX, int(row["heat"]) + delta))
            status = "faded" if new_h <= 0 else "active"
            conn.execute(
                "UPDATE rumor SET heat = ?, status = CASE WHEN status='debunked' THEN 'debunked' ELSE ? END, updated_at = ? WHERE id = ?",
                (new_h, status, int(time.time()), rumor_id),
            )
        return new_h

    def set_status(self, rumor_id: int, status: str) -> None:
        with get_conn() as conn:
            conn.execute(
                "UPDATE rumor SET status = ?, updated_at = ? WHERE id = ?",
                (status, int(time.time()), rumor_id),
            )

    def decay_daily(self) -> int:
        """每游戏日热度衰减，返回冷却成 faded 的条数。"""
        now = int(time.time())
        with get_conn() as conn:
            conn.execute(
                "UPDATE rumor SET heat = MAX(0, heat - ?), updated_at = ? WHERE status = 'active'",
                (HEAT_DECAY_PER_DAY, now),
            )
            cur = conn.execute(
                "UPDATE rumor SET status = 'faded' WHERE status = 'active' AND heat <= 0"
            )
            return cur.rowcount or 0

    # ---------- rumor_knowledge ----------

    def add_knowledge(
        self, rumor_id: int, animal_id: str, version: str, hops: int, day: int
    ) -> None:
        now = int(time.time())
        with get_conn() as conn:
            conn.execute(
                """INSERT INTO rumor_knowledge
                   (rumor_id, animal_id, version, hops, learned_day, told_count, created_at)
                   VALUES (?, ?, ?, ?, ?, 0, ?)
                   ON CONFLICT(rumor_id, animal_id) DO UPDATE SET
                     version = excluded.version""",
                (rumor_id, animal_id, version, hops, day, now),
            )

    def knows(self, rumor_id: int, animal_id: str) -> Optional[Dict]:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM rumor_knowledge WHERE rumor_id = ? AND animal_id = ?",
                (rumor_id, animal_id),
            ).fetchone()
        if not row:
            return None
        return {"version": row["version"], "hops": int(row["hops"]),
                "told_count": int(row["told_count"])}

    def bump_told(self, rumor_id: int, animal_id: str) -> None:
        with get_conn() as conn:
            conn.execute(
                "UPDATE rumor_knowledge SET told_count = told_count + 1 WHERE rumor_id = ? AND animal_id = ?",
                (rumor_id, animal_id),
            )

    def known_by(self, animal_id: str, min_heat: int = SPREAD_MIN_HEAT) -> List[Dict]:
        """该 NPC 知道的、仍算热门的话题（含 ta 的版本），按热度降序。"""
        with get_conn() as conn:
            rows = conn.execute(
                """SELECT r.*, k.version AS kversion, k.hops AS khops, k.told_count AS ktold
                   FROM rumor_knowledge k JOIN rumor r ON r.id = k.rumor_id
                   WHERE k.animal_id = ? AND r.status = 'active' AND r.heat >= ?
                   ORDER BY r.heat DESC""",
                (animal_id, min_heat),
            ).fetchall()
        out = []
        for r in rows:
            out.append({
                "rumor": Rumor.from_row(r),
                "version": r["kversion"],
                "hops": int(r["khops"]),
                "told_count": int(r["ktold"]),
            })
        return out

    # ---------- rumor_belief（信念判定结果，每人每条只判一次）----------

    def get_belief(self, rumor_id: int, animal_id: str) -> Optional[Dict]:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM rumor_belief WHERE rumor_id = ? AND animal_id = ?",
                (rumor_id, animal_id),
            ).fetchone()
        if not row:
            return None
        return {"state": row["state"], "source_id": row["source_id"],
                "score": float(row["score"]), "judged_day": int(row["judged_day"])}

    def set_belief(self, rumor_id: int, animal_id: str, state: str,
                   source_id: str = "", score: float = 0.0, day: int = 0) -> bool:
        """写入判定结果。已存在则不覆盖（判定即锁定），返回是否为本次新写入。"""
        now = int(time.time())
        with get_conn() as conn:
            cur = conn.execute(
                """INSERT INTO rumor_belief
                   (rumor_id, animal_id, state, source_id, score, judged_day, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(rumor_id, animal_id) DO NOTHING""",
                (rumor_id, animal_id, state, source_id, score, day, now),
            )
            return (cur.rowcount or 0) > 0

    def belief_rows_for(self, voter_id: str, subject_id: str) -> List[Dict]:
        """某选民对某主角、已判定为「信」的话题（含情感），用于选情结算。"""
        with get_conn() as conn:
            rows = conn.execute(
                """SELECT r.sentiment AS sentiment
                   FROM rumor_belief b JOIN rumor r ON r.id = b.rumor_id
                   WHERE b.animal_id = ? AND r.subject_id = ?
                     AND b.state = 'believed' AND r.status = 'active'
                     AND r.sentiment IN ('smear', 'praise')""",
                (voter_id, subject_id),
            ).fetchall()
        return [{"sentiment": r["sentiment"]} for r in rows]


# ──────────────────────────────────────────────
# RumorManager：生成 / 传播（变味）
# ──────────────────────────────────────────────

_MUTATE_PROMPT = """你是「{name}」（{species}），性格：{personality}。此刻你的心情：{mood}。
你刚从别人嘴里听到一则关于「{subject}」的小道消息：
「{version}」

请用你自己的口吻，把这条消息在心里复述成一句话（就像你之后会转述给别人的版本）。
要求：
- 保留核心事件，但允许按你的性格和心情自然地夸大、揣测、加情绪或打折扣（八卦本就会变味）；
- 一句话，25 字以内，第一人称转述口气（如"听说…""我跟你讲…"）；
- 只输出这句话，不要引号、不要解释。"""


class RumorManager:
    def __init__(
        self,
        store: RumorStore,
        llm: LLMClient,
        world_store,
        *,
        name_of=None,       # callable(animal_id)->显示名
        persona_of=None,    # callable(animal_id)->persona dict
        mood_store=None,
    ) -> None:
        self.store = store
        self.llm = llm
        self.world = world_store
        self._name_of = name_of or (lambda x: x)
        self._persona_of = persona_of or (lambda x: {})
        self.mood_store = mood_store

    def subject_label(self, subject_id: str) -> str:
        if subject_id == "player":
            return "那位旅人（镇长候选人）"
        return self._name_of(subject_id)

    # ---------- 生成 ----------

    def generate(
        self,
        subject_id: str,
        content: str,
        *,
        sentiment: str = "neutral",
        truth: int = 1,
        origin: str = "auto",
        game_day: int = 0,
        initial_knowers: Optional[List[str]] = None,
        heat: int = 45,
    ) -> Optional[int]:
        """创建一条话题并播种初始知情者。auto 来源当日同主角同情感去重。"""
        if origin == "auto":
            dup = self.store.similar_today(subject_id, sentiment, game_day)
            if dup:
                self.store.adjust_heat(dup.id, 10)  # 又发生一次同类事 → 加温
                return dup.id
        rid = self.store.create(
            subject_id, content, sentiment=sentiment, truth=truth,
            heat=heat, origin=origin, game_day=game_day,
        )
        for aid in (initial_knowers or []):
            if aid and aid != subject_id and aid != "player":
                self.store.add_knowledge(rid, aid, content, hops=0, day=game_day)
        # 进世界事件流（供反思感知）
        try:
            self.world.add(actor="rumor",
                           description=f"镇上开始流传：{content}", game_time="")
        except Exception:
            pass
        log.info("[rumor] gen #%d subject=%s sent=%s: %s", rid, subject_id, sentiment, content[:30])
        return rid

    # ---------- 传播（变味） ----------

    def pick_gossip_for(self, speaker_id: str) -> Optional[Dict]:
        """挑一条 speaker 最想传的热门话题（未传够 TELL_LIMIT 次）。"""
        for item in self.store.known_by(speaker_id):
            if item["told_count"] < TELL_LIMIT:
                return item
        return None

    async def propagate(
        self, rumor_id: int, from_id: str, to_id: str, game_day: int
    ) -> Optional[Dict]:
        """from 把话题传给 to：to 学到并按性格+心情把它"变味"记下。

        返回 {rumor_id, subject_id, sentiment, to_version, reached_subject} 或 None。
        """
        r = self.store.get(rumor_id)
        if not r or r.status != "active":
            return None
        from_k = self.store.knows(rumor_id, from_id)
        src_version = from_k["version"] if from_k else r.content
        hops = (from_k["hops"] if from_k else 0) + 1

        # 变味改写（LLM，一句）
        to_version = await self._mutate(to_id, r.subject_id, src_version)

        self.store.add_knowledge(rumor_id, to_id, to_version, hops=hops, day=game_day)
        self.store.bump_told(rumor_id, from_id)
        new_heat = self.store.adjust_heat(rumor_id, HEAT_SPREAD_GAIN)

        reached_subject = (to_id == r.subject_id)
        log.info("[rumor] spread #%d %s→%s hop=%d heat=%d reached=%s",
                 rumor_id, from_id, to_id, hops, new_heat, reached_subject)
        return {
            "rumor_id": rumor_id,
            "subject_id": r.subject_id,
            "sentiment": r.sentiment,
            "truth": r.truth,
            "to_version": to_version,
            "reached_subject": reached_subject,
        }

    async def _mutate(self, animal_id: str, subject_id: str, version: str) -> str:
        persona = self._persona_of(animal_id) or {}
        mood_txt = "平静"
        if self.mood_store is not None:
            try:
                mood_txt = str(self.mood_store.snapshot(animal_id).get("label", "平静"))
            except Exception:
                pass
        prompt = _MUTATE_PROMPT.format(
            name=self._name_of(animal_id),
            species=persona.get("species", "怪物"),
            personality=persona.get("personality", "普通"),
            mood=mood_txt,
            subject=self.subject_label(subject_id),
            version=version,
        )
        try:
            out = await self.llm.chat([{"role": "user", "content": prompt}],
                                      max_tokens=60, temperature=0.95)
            out = out.strip().strip("「」\"'").splitlines()[0][:40]
            return out or version
        except Exception as e:
            log.warning("[rumor] mutate 失败: %s", e)
            return version
