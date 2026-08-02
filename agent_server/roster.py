"""NPC 在场名单与备选池。

背景：data/animals/ 下的 persona JSON 会被 load_all_personas() 无差别全部加载，
于是备选池的 NPC 也会照常参与选举计票、危机抽签、每日反思——可前端场景里
根本没有他们的节点，玩家看不见也找不着，只会白烧 LLM 额度并让票数莫名其妙。
所以「镇上有谁」必须是一份显式名单，而不是「data 目录里有几个文件」。

两种状态：
  present  在镇上，参与一切
  reserve  备选池 / 已离镇，不参与任何镇上系统

离镇者的好感不清零而是冻结存档（frozen_value），日后若再迁回镇上，
按 RETURN_MULTIPLIER 打折恢复——既不让玩家多年的经营一笔勾销，
也不至于让"老朋友回来了"直接白送一个顶档关系。
"""
from __future__ import annotations

import time
import logging
from typing import Dict, List, Optional

from db import get_conn

log = logging.getLogger("agent_server")

PRESENT = "present"
RESERVE = "reserve"

# 镇上常驻人数。轮换恒定走 1 进 1，总数不变。
TOWN_SIZE = 6

# 回归时好感的恢复系数。
RETURN_MULTIPLIER = 0.5

# 迁入后至少住满多少天才可能被选中离镇。
# 一届约 30 天，取 45 天 = 一届半，保证新人至少完整经历一届才可能再走。
MIN_STAY_DAYS = 45

# 开局在场名单。顺序即入镇顺序，不参与任何逻辑，仅作可读性。
DEFAULT_PRESENT = [
    "bear_baker",
    "fox_postman",
    "herbalist_cui",
    "pirate_lao",
    "mystic_xuan",
    "traveler_lan",
]

# 每个 NPC 自带的住所。轮换时新人不新建房子，而是迁入离镇者的宅子，
# 只换门牌（building.display_name）——因为 building_id 被 path_network、
# 日程、spawners 三处引用，改 id 会牵一发动全身。
HOME_OF: Dict[str, str] = {
    "bear_baker":    "home_bear",
    "fox_postman":   "home_fox",
    "herbalist_cui": "home_cui",
    "pirate_lao":    "home_pirate",
    "mystic_xuan":   "home_mystic",
    "traveler_lan":  "home_lan",
}

# 各住所对应的出生坐标。原先写死在 main.tscn 的节点 position 上，
# 改为动态实例化后必须由后端给出——绑住所而非绑人，因为轮换时
# 新人接手的是房子，理应出现在那所房子门口。
HOME_SPAWN: Dict[str, List[float]] = {
    "home_bear":   [200.0, 240.0],
    "home_fox":    [992.0, 240.0],
    "home_cui":    [-103.0, 229.0],
    "home_lan":    [728.0, 714.0],
    "home_pirate": [700.0, 240.0],
    "home_mystic": [1145.0, 425.0],
}

# 备选池 NPC 的 persona 日程里写的是各自的"理想住所"（home_boar / home_mole /
# home_diva），可地图上并没有这些房子——他们是迁入离镇者的宅子的。
# 迁入时把日程里的占位地点整体替换成实际接手的 building_id。
PLACEHOLDER_HOME: Dict[str, str] = {
    "boar_shi": "home_boar",
    "mole_tu":  "home_mole",
    "diva_mei": "home_diva",
}


def rebind_home(persona: Dict, home_id: str) -> Dict:
    """把 persona 日程中的占位住所替换为实际 building_id。

    不改写磁盘上的 JSON —— 房子归属是存档状态，写回 data/ 会污染新开的局。
    """
    aid = persona.get("id", "")
    placeholder = PLACEHOLDER_HOME.get(aid)
    if not placeholder or not home_id:
        return persona
    for key in ("schedule", "schedule_weekend"):
        for slot in persona.get(key) or []:
            if slot.get("location") == placeholder:
                slot["location"] = home_id
    return persona


class RosterStore:
    """在场名单读写。首次访问时按 DEFAULT_PRESENT 落初值。"""

    def __init__(self, all_ids: List[str]):
        self.all_ids = list(all_ids)
        self._ensure_seeded()

    def _ensure_seeded(self) -> None:
        with get_conn() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM npc_roster").fetchone()
            if row and int(row["n"]) > 0:
                return
            now = int(time.time())
            for aid in self.all_ids:
                present = aid in DEFAULT_PRESENT
                conn.execute(
                    """INSERT OR IGNORE INTO npc_roster
                       (animal_id, status, home_id, joined_day, left_day,
                        frozen_value, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (aid, PRESENT if present else RESERVE,
                     HOME_OF.get(aid, ""), 0, -1, 0, now),
                )

    # ---- 查询 ----

    def _rows(self, status: Optional[str] = None) -> List[Dict]:
        # 会话隔离：每个 sid 有独立的 db 文件，新会话的 npc_roster 是空表。
        # 构造时只 seed 过默认库，所以每次读取前都要确认当前库已落初值，
        # 否则新开的会话会得到一座空镇。
        self._ensure_seeded()
        sql = "SELECT * FROM npc_roster"
        args: tuple = ()
        if status:
            sql += " WHERE status = ?"
            args = (status,)
        with get_conn() as conn:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]

    def present_ids(self) -> List[str]:
        """镇上的 NPC。所有"遍历全部 NPC"的地方都该改用这个。"""
        return sorted(r["animal_id"] for r in self._rows(PRESENT))

    def reserve_ids(self) -> List[str]:
        return sorted(r["animal_id"] for r in self._rows(RESERVE))

    def is_present(self, animal_id: str) -> bool:
        self._ensure_seeded()
        with get_conn() as conn:
            row = conn.execute(
                "SELECT status FROM npc_roster WHERE animal_id = ?",
                (animal_id,),
            ).fetchone()
        return bool(row) and row["status"] == PRESENT

    def home_of(self, animal_id: str) -> str:
        self._ensure_seeded()
        with get_conn() as conn:
            row = conn.execute(
                "SELECT home_id FROM npc_roster WHERE animal_id = ?",
                (animal_id,),
            ).fetchone()
        return row["home_id"] if row else ""

    def get(self, animal_id: str) -> Optional[Dict]:
        self._ensure_seeded()
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM npc_roster WHERE animal_id = ?",
                (animal_id,),
            ).fetchone()
        return dict(row) if row else None

    # ---- 轮换 ----

    def depart(self, animal_id: str, game_day: int, affection_value: int) -> Dict:
        """某人离镇：转 reserve，好感冻结存档，房子腾出。"""
        home = self.home_of(animal_id)
        with get_conn() as conn:
            conn.execute(
                """UPDATE npc_roster
                   SET status = ?, left_day = ?, frozen_value = ?,
                       home_id = '', updated_at = ?
                   WHERE animal_id = ?""",
                (RESERVE, game_day, int(affection_value),
                 int(time.time()), animal_id),
            )
        return {"animal_id": animal_id, "home_id": home,
                "frozen_value": int(affection_value), "left_day": game_day}

    def arrive(self, animal_id: str, game_day: int, home_id: str) -> Dict:
        """某人迁入：转 present，接手指定房子。

        返回 restored_value —— 若此人从未来过则为 0（新人从零开始），
        若是回归的老朋友则是冻结值打折后的结果。
        """
        rec = self.get(animal_id) or {}
        frozen = int(rec.get("frozen_value") or 0)
        restored = int(frozen * RETURN_MULTIPLIER) if frozen > 0 else frozen
        with get_conn() as conn:
            conn.execute(
                """UPDATE npc_roster
                   SET status = ?, joined_day = ?, left_day = -1,
                       home_id = ?, frozen_value = 0, updated_at = ?
                   WHERE animal_id = ?""",
                (PRESENT, game_day, home_id, int(time.time()), animal_id),
            )
        return {"animal_id": animal_id, "home_id": home_id,
                "restored_value": restored, "was_frozen": frozen}

    def snapshot(self, personas: Optional[Dict] = None) -> List[Dict]:
        """在场名单快照，供前端实例化 NPC 节点。

        schedule_override 是关键：备选池 NPC 的 persona 磁盘文件里写的是
        占位住所（home_boar 等），前端直接读文件会拿到地图上不存在的地点，
        NPC 会走不到家。这里把重绑定后的日程一并下发。
        """
        out: List[Dict] = []
        all_personas = personas
        if all_personas is None:
            all_personas = {}
        for aid in self.present_ids():
            home = self.home_of(aid)
            entry = {
                "animal_id": aid,
                "home_id": home,
                "spawn": HOME_SPAWN.get(home, [600.0, 350.0]),
                "persona_file": f"res://data/animals/{aid}.json",
            }
            if aid in PLACEHOLDER_HOME and aid in all_personas:
                p = all_personas[aid]
                entry["schedule_override"] = {
                    "schedule": p.get("schedule") or [],
                    "schedule_weekend": p.get("schedule_weekend") or [],
                }
            out.append(entry)
        return out


# 公共设施：主人一走就停业，而不是转交给新人。
# 交给新人等于什么都没发生；停业才让「谁来接手面包店」变成玩家任期内
# 必须解决的镇务，让 NPC 离开真正在镇上留下一个洞。
PUBLIC_FACILITY: Dict[str, str] = {
    "bear_baker":  "bakery",
    "fox_postman": "post_office",
}


def rotate_on_term_end(
    roster: "RosterStore",
    affection_store,
    game_day: int,
    reserve_pool: Optional[List[str]] = None,
) -> Dict:
    """换届轮换：好感最高者离镇，备选池补一人。

    挑好感最高的走，是这套设计的要害——随机走人只是噪音，
    走掉玩家花了整个任期经营的那一个，才既是告别也是重来。
    """
    present = roster.present_ids()
    if len(present) <= 1:
        return {"skipped": "镇上人太少"}

    pool = list(reserve_pool if reserve_pool is not None else roster.reserve_ids())
    if not pool:
        return {"skipped": "备选池已空"}

    scored = []
    for aid in present:
        # 上届才迁入的人不参与挑选：刚办完欢迎就送别，玩家来不及跟他
        # 建立任何关系，轮换会退化成走马灯。至少让他在镇上待满一届。
        rec = roster.get(aid) or {}
        joined = int(rec.get("joined_day") or 0)
        if joined > 0 and game_day - joined < MIN_STAY_DAYS:
            continue
        try:
            scored.append((int(affection_store.get(aid)), aid))
        except Exception:
            continue
    if not scored:
        return {"skipped": "无可轮换的人（都是刚迁入的）"}
    # 同分时取 id 靠前者，保证结果可复现（否则测试与存档回放会飘）
    scored.sort(key=lambda t: (-t[0], t[1]))
    leave_value, leaver = scored[0]

    dep = roster.depart(leaver, game_day, leave_value)
    newcomer = sorted(pool)[0]

    # 回归的老居民优先住回自己原来的宅子；只有原宅已被别人占着，
    # 才去接手这次空出来的那间。否则会出现"苔老板回来了却住在邮局、
    # 面包店照旧停业"这种说不通的局面。
    home = dep["home_id"]
    own = HOME_OF.get(newcomer, "")
    if own and own not in {roster.home_of(a) for a in roster.present_ids()}:
        home = own
    arr = roster.arrive(newcomer, game_day, home)

    if arr["restored_value"] != 0:
        try:
            cur = int(affection_store.get(newcomer))
            affection_store.adjust(newcomer, arr["restored_value"] - cur)
        except Exception as e:
            log.warning("[roster] 回归好感恢复失败 %s: %s", newcomer, e)

    return {
        "leaver": leaver,
        "leaver_affection": leave_value,
        "newcomer": newcomer,
        "home_id": home,
        "restored_value": arr["restored_value"],
        # 离镇者若经营着公共设施，该设施停业，等玩家想办法重开
        "closed_facility": PUBLIC_FACILITY.get(leaver, ""),
        # 主人回来了，自然重新开张——前提是他住回了自己的铺子
        "reopened_facility": (
            PUBLIC_FACILITY.get(newcomer, "")
            if home == HOME_OF.get(newcomer, "") else ""
        ),
        # 赠别礼：好感越深礼越重，这是玩家一个任期经营的回执
        "farewell_gift": _farewell_gift(leave_value),
        # 前端据此实例化新人节点、改门牌
        "spawn": HOME_SPAWN.get(home, [600.0, 350.0]),
        "persona_file": f"res://data/animals/{newcomer}.json",
        "nameplate": "",   # 由 main 用 persona 名字填
        "game_day": game_day,
    }


def _farewell_gift(value: int) -> str:
    if value >= 85:
        return "heart_pot"
    if value >= 60:
        return "gem_green"
    if value >= 35:
        return "coin_purse"
    return ""
