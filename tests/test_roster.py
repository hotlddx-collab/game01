#!/usr/bin/env python3
"""NPC 在场名单 / 备选池 / 换届轮换测试。

运行: ./agent_server/.venv/bin/python tests/test_roster.py
覆盖：
1. 初始名单：在场 6 人、备选池 3 人
2. 轮换：走 1 进 1，总数恒为 6
3. 房子交接：新人接手离镇者的宅子，日程占位地点被重绑定
4. 好感冻结存档 + 回归打对折
5. 备选池 NPC 不得混入镇上任何系统（危机 / 任务）
"""

import os
import sys
import copy
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "agent_server"))

PASS = "\033[92m✅ PASS\033[0m"
FAIL = "\033[91m❌ FAIL\033[0m"

results = []


def check(name, condition, detail=""):
    ok = bool(condition)
    print(f"{PASS if ok else FAIL}  {name}" + (f"  ({detail})" if detail else ""))
    results.append(ok)


TEST_DB = Path(tempfile.mkdtemp()) / "test_roster.db"
os.environ["TOWN_DB_PATH"] = str(TEST_DB)

import db  # noqa: E402
db.current_db_path.set(TEST_DB)
db.init_schema(TEST_DB)

import roster  # noqa: E402
from roster import RosterStore, rebind_home, TOWN_SIZE, RETURN_MULTIPLIER  # noqa: E402
from personas import load_all_personas  # noqa: E402

ALL = load_all_personas()
R = RosterStore(list(ALL.keys()))

print("=== 1. 初始名单 ===")
present = R.present_ids()
reserve = R.reserve_ids()
check("在场恰好 6 人", len(present) == TOWN_SIZE, str(present))
check("备选池 3 人", sorted(reserve) == ["boar_shi", "diva_mei", "mole_tu"], str(reserve))
check("在场+备选 == 全部 persona", len(present) + len(reserve) == len(ALL))
check("备选池不算在场", all(not R.is_present(x) for x in reserve))
check("在场者都有住所", all(R.home_of(x) for x in present))

print("\n=== 2. 每个在场者的住所都真实存在于日程 ===")
for aid in present:
    locs = {s["location"] for s in ALL[aid]["schedule"]}
    check(f"{aid} 的住所在其日程中", R.home_of(aid) in locs, R.home_of(aid))

print("\n=== 3. 轮换：走 1 进 1 ===")
dep = R.depart("bear_baker", game_day=30, affection_value=88)
check("离镇后转入备选池", not R.is_present("bear_baker"))
check("离镇者腾出房子", dep["home_id"] == "home_bear", dep["home_id"])
check("离镇瞬间镇上只剩 5 人", len(R.present_ids()) == TOWN_SIZE - 1)

arr = R.arrive("boar_shi", game_day=30, home_id=dep["home_id"])
check("新人迁入后总数恢复 6", len(R.present_ids()) == TOWN_SIZE, str(R.present_ids()))
check("新人接手离镇者的宅子", R.home_of("boar_shi") == "home_bear")
check("新人好感从零开始", arr["restored_value"] == 0, str(arr))

print("\n=== 4. 日程重绑定（占位住所 → 实际房子）===")
p = rebind_home(copy.deepcopy(ALL["boar_shi"]), "home_bear")
locs = {s["location"] for s in p["schedule"]}
check("占位 home_boar 已消失", "home_boar" not in locs, str(sorted(locs)))
check("已指向接手的 home_bear", "home_bear" in locs)
wk = {s["location"] for s in (p.get("schedule_weekend") or [])}
check("周末日程也已重绑定", "home_boar" not in wk, str(sorted(wk)))
check("不改写磁盘 persona", "home_boar" in
      {s["location"] for s in ALL["boar_shi"]["schedule"]})

print("\n=== 5. 好感冻结与回归打折 ===")
dep2 = R.depart("boar_shi", game_day=60, affection_value=40)
back = R.arrive("bear_baker", game_day=60, home_id=dep2["home_id"])
check("回归时读到冻结值", back["was_frozen"] == 88, str(back))
check("回归好感打对折", back["restored_value"] == int(88 * RETURN_MULTIPLIER), str(back))
check("回归后不再重复打折", (R.get("bear_baker") or {}).get("frozen_value") == 0)
check("轮换两轮后总数仍是 6", len(R.present_ids()) == TOWN_SIZE, str(R.present_ids()))

print("\n=== 6. 敌对好感冻结不该被洗白 ===")
R.depart("herbalist_cui", game_day=70, affection_value=-30)
rec = R.get("herbalist_cui") or {}
check("负好感原样冻结", rec.get("frozen_value") == -30, str(rec.get("frozen_value")))
b2 = R.arrive("herbalist_cui", game_day=80, home_id="home_cui")
check("负好感回归不打折（不靠离镇洗白）", b2["restored_value"] == -30, str(b2))

print("\n=== 7. 备选池不得混入镇上系统 ===")
import json  # noqa: E402
active = {k: v for k, v in ALL.items() if k in set(R.present_ids())}


class _FakeCrisis:
    personas = active
    present_provider = None

    def _all_present(self, entry):
        from crisis import CrisisManager
        return CrisisManager._all_present(self, entry)


raw = json.loads((ROOT / "data/world/crises.json").read_text(encoding="utf-8"))
raw = {k: v for k, v in raw.items() if not k.startswith("_")}
fc = _FakeCrisis()
visible = [k for k, v in raw.items() if fc._all_present(v)]
hidden = [k for k in raw if k not in visible]
check("涉及备选池的危机被隐藏", len(hidden) > 0, str(hidden))
check("被隐藏的危机确有不在场当事人",
      all(any(p not in active for p in raw[k]["parties"]) for k in hidden))
check("可见危机的当事人全部在场",
      all(all(p in active for p in raw[k]["parties"]) for k in visible))

from quests import QuestEngine, QuestStore  # noqa: E402
qe = QuestEngine(QuestStore(TEST_DB))
qe.is_present = R.is_present
qdefs = {k: v for k, v in qe._defs.items()}
off = [k for k, v in qdefs.items() if not qe._npc_available(v)]
check("备选池 NPC 的任务不可派发", len(off) > 0, f"{len(off)} 个")
check("被挡任务确因当事人不在场",
      all(not R.is_present(qdefs[k]["npc_id"]) or
          not R.is_present((qdefs[k].get("requires") or {}).get("target_npc") or
                           qdefs[k]["npc_id"])
          for k in off))

print("\n=== 8. 换届轮换编排 ===")
from affection import AffectionStore  # noqa: E402
import roster as _r  # noqa: E402

DB2 = Path(tempfile.mkdtemp()) / "test_rotate.db"
db.current_db_path.set(DB2)
db.init_schema(DB2)
A = AffectionStore()
R2 = RosterStore(list(ALL.keys()))
for aid, v in [("bear_baker", 90), ("fox_postman", 70),
               ("pirate_lao", 40), ("mystic_xuan", 55)]:
    A.adjust(aid, v)

r1 = _r.rotate_on_term_end(R2, A, game_day=30)
check("走的是好感最高的那位", r1["leaver"] == "bear_baker", str(r1["leaver"]))
check("离镇者的公共设施停业", r1["closed_facility"] == "bakery", r1["closed_facility"])
check("顶档好感给最重的赠别礼", r1["farewell_gift"] == "heart_pot", r1["farewell_gift"])
check("新人接手空出的宅子", R2.home_of(r1["newcomer"]) == r1["home_id"])
check("轮换后总数仍是 6", len(R2.present_ids()) == TOWN_SIZE)

joined = {r1["newcomer"]: 30}
ok_stay, ok_size, ok_home = True, True, True
for i in range(2, 9):
    day = i * 30
    r = _r.rotate_on_term_end(R2, A, game_day=day)
    if r.get("skipped"):
        continue
    prev = joined.get(r["leaver"], 0)
    if prev > 0 and day - prev < _r.MIN_STAY_DAYS:
        ok_stay = False
    joined[r["newcomer"]] = day
    if len(R2.present_ids()) != TOWN_SIZE:
        ok_size = False
    homes = [R2.home_of(a) for a in R2.present_ids()]
    if len(homes) != len(set(homes)):
        ok_home = False

check("连轮 8 届无人住不满一届就被赶走", ok_stay)
check("连轮 8 届总数恒为 6", ok_size)
check("连轮 8 届无两人共用一间房", ok_home)

print("\n=== 9. 备选池耗尽后的兜底 ===")
DB3 = Path(tempfile.mkdtemp()) / "test_pool.db"
db.current_db_path.set(DB3)
db.init_schema(DB3)
R3 = RosterStore(list(ALL.keys()))
A3 = AffectionStore()
A3.adjust("bear_baker", 50)
empty = _r.rotate_on_term_end(R3, A3, game_day=30, reserve_pool=[])
check("备选池空时跳过轮换而非崩溃", empty.get("skipped"), str(empty))
check("跳过时名单不变", len(R3.present_ids()) == TOWN_SIZE)

print("\n=== 10. 会话隔离：新会话不该继承别人的轮换结果 ===")
# personas 是进程级全局、多会话共享；"谁在镇上"却是每会话的存档状态。
# 曾经在启动时按默认库的名单裁剪 personas，导致新会话拿到错误的镇民。
DB4 = Path(tempfile.mkdtemp()) / "sess_a.db"
DB5 = Path(tempfile.mkdtemp()) / "sess_b.db"
db.current_db_path.set(DB4)
db.init_schema(DB4)
RA = RosterStore(list(ALL.keys()))
AA = AffectionStore()
AA.adjust("bear_baker", 95)
_r.rotate_on_term_end(RA, AA, game_day=30)
a_present = RA.present_ids()

db.current_db_path.set(DB5)
db.init_schema(DB5)
RB = RosterStore(list(ALL.keys()))
b_present = RB.present_ids()

check("会话 A 轮换后苔老板已离镇", "bear_baker" not in a_present, str(a_present))
check("会话 B 仍是初始 6 人", sorted(b_present) == sorted(
    ["bear_baker", "fox_postman", "herbalist_cui",
     "mystic_xuan", "pirate_lao", "traveler_lan"]), str(b_present))
check("两个会话名单互不影响", set(a_present) != set(b_present))
check("快照能取到全量 persona 的名字（不依赖在场表）",
      all((ALL.get(e["animal_id"]) or {}).get("name")
          for e in RA.snapshot(ALL)))

print("\n=== 结果汇总 ===")
ok = sum(1 for r in results if r)
print(f"通过 {ok}/{len(results)}")
sys.exit(0 if ok == len(results) else 1)
