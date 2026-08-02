#!/usr/bin/env python3
"""物品可达性与索要链路测试。

跑法: python3 tests/test_item_source.py

覆盖玩家实测的四个问题：
1. 小蓝要水壶，提示是否指向真实持有者
2. 深绿好感能否真的向持有者要到水壶
3. 焰仔要火卷轴同理
4. 送礼回赠是否会因好感变高而丢失低档物品
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "agent_server"))

TEST_DB = os.path.join(tempfile.mkdtemp(), "t.db")
os.environ["TOWN_DB_PATH"] = TEST_DB

PASS = "\033[92m✅ PASS\033[0m"
FAIL = "\033[91m❌ FAIL\033[0m"
results = []


def check(name, cond, detail=""):
    print(f"{PASS if cond else FAIL}  {name}" + (f"  ({detail})" if detail else ""))
    results.append(bool(cond))


import db  # noqa: E402
db.current_db_path.set(db.Path(TEST_DB))
db.init_schema(TEST_DB)

import personas  # noqa: E402
import items as items_module  # noqa: E402
from items_source import ItemSourceIndex  # noqa: E402
from affection import at_least  # noqa: E402

P = personas.load_all_personas()
idx = ItemSourceIndex(P)
QUESTS = json.load(open(ROOT / "data" / "world" / "quests.json", encoding="utf-8"))

print("=== 1. 全部任务需求物可达 ===")
need = set()
for v in QUESTS.values():
    if isinstance(v, dict) and isinstance(v.get("requires"), dict):
        i = v["requires"].get("item_id")
        if i:
            need.add(i)
bad = idx.unreachable_items(sorted(need))
check("43 件任务需求物全部有来源", not bad, f"无来源={bad}" if bad else f"{len(need)} 件全可达")

print("\n=== 2. 玩家实测物品的真实持有者 ===")
wp = idx.holders("water_pot")
sf = idx.holders("scroll_fire")
check("水壶有持有者", len(wp) > 0, str(wp))
check("火卷轴有持有者", len(sf) > 0, str(sf))
check("水壶持有者不是小翠（旧提示在说谎）",
      all(h["npc_id"] != "herbalist_cui" for h in wp),
      "真实持有者=" + ",".join(h["name"] for h in wp))

print("\n=== 3. 来源提示指向真实持有者 ===")
hint_wp = idx.hint_for("water_pot", asker_id="traveler_lan")
hint_sf = idx.hint_for("scroll_fire", asker_id="fox_postman")
print("  水壶提示:", hint_wp)
print("  火卷轴提示:", hint_sf)
check("水壶提示含真实持有者名字",
      any(h["name"] in hint_wp for h in wp))
check("火卷轴提示含真实持有者名字",
      any(h["name"] in hint_sf for h in sf))

print("\n=== 4. 索要可送列表包含回礼池（修复前的核心 bug）===")


def giveable_of(npc_id, level):
    p = P[npc_id]
    prefs = p.get("gift_prefs", {}) or {}
    g = set(prefs.get("loves", []))
    if p.get("signature_gift"):
        g.add(p["signature_gift"])
    g |= set(p.get("return_gifts", []) or [])
    if at_least(level, "fond"):
        g |= set(p.get("mid_return_gifts", []) or [])
    if at_least(level, "close"):
        g |= set(p.get("rare_return_gifts", []) or [])
    return g


check("深绿好感可向煊赫要到水壶",
      "water_pot" in giveable_of("mystic_xuan", "intimate"))
check("深绿好感可向小蓝要到火卷轴",
      "scroll_fire" in giveable_of("traveler_lan", "intimate"))
check("friendly 档要不到 rare 物（门槛仍在）",
      "scroll_fire" not in giveable_of("traveler_lan", "friendly"))

print("\n=== 5. 回礼池累加：好感变高不该丢失低档物品 ===")


def return_pool(npc_id, level):
    p = P[npc_id]
    gp = p.get("gift_prefs", {}) or {}
    mine = set(gp.get("loves", []) or []) | set(gp.get("likes", []) or [])
    pool = [i for i in (p.get("return_gifts") or []) if i not in mine]
    if at_least(level, "fond"):
        pool += [i for i in (p.get("mid_return_gifts") or []) if i not in mine]
    if at_least(level, "close"):
        pool += [i for i in (p.get("rare_return_gifts") or []) if i not in mine]
    return pool


love_pool = return_pool("mystic_xuan", "intimate")
check("煊赫顶档回礼池仍含水壶（修复前会消失）",
      "water_pot" in love_pool, str(love_pool))
for npc in P:
    lp = return_pool(npc, "intimate")
    cp = return_pool(npc, "neutral")
    if not set(cp).issubset(set(lp)):
        check(f"{npc} 低档物品在顶档丢失", False)
        break
else:
    check("所有 NPC 的低档回礼在顶档均保留", True)

print("\n=== 6. 防刷好感规则仍生效 ===")
viol = []
for npc, p in P.items():
    gp = p.get("gift_prefs", {}) or {}
    mine = set(gp.get("loves", []) or []) | set(gp.get("likes", []) or [])
    for f in ("return_gifts", "mid_return_gifts", "rare_return_gifts"):
        for i in (p.get(f) or []):
            if i in mine:
                viol.append(f"{npc}.{f}:{i}")
check("回礼池不含 NPC 自己喜欢的物品", not viol, str(viol))

print("\n=== 7. 地图可捡物不该占用 NPC 回礼位 ===")
ground_in_pool = []
for npc, p in P.items():
    for f in ("return_gifts", "mid_return_gifts", "rare_return_gifts"):
        for i in (p.get(f) or []):
            if idx.is_ground_item(i):
                ground_in_pool.append(f"{npc}.{f}:{i}")
check("回礼池不含地图可捡的低价物", not ground_in_pool, str(ground_in_pool))

print("\n=== 8. 索要清单不得包含 NPC 自己 loves 的物品 ===")
# 玩家实测截图：焰仔说「我手里倒是有个火卷轴」并真的送了出去，
# 可火卷轴的唯一来源是小蓝——他根本没有。
# 根因：giveable 无条件并入了 loves。loves 是「喜欢收到什么」，不是「手上有什么」。
# 更糟的是这打通了刷好感闭环：要走 → 再送回去（他 loves）→ 白加好感。
import re  # noqa: E402

src = (ROOT / "agent_server" / "agent.py").read_text(encoding="utf-8")
m = re.search(r"def _handle_gift_request\(.*?\n    def ", src, re.S)
body = m.group(0) if m else ""
check("giveable 不再并入 loves",
      "giveable = set(prefs.get(\"loves\"" not in body
      and "giveable = set()" in body)

# 逐个 NPC 复核：可索要清单 ∩ 自己的 loves/likes，只允许 signature_gift
leak = []
for npc, p in P.items():
    gp = p.get("gift_prefs", {}) or {}
    mine = set(gp.get("loves", []) or []) | set(gp.get("likes", []) or [])
    sig = p.get("signature_gift", "")
    give = set()
    for f in ("return_gifts", "mid_return_gifts", "rare_return_gifts"):
        give |= set(p.get(f) or [])
    for i in (give & mine):
        if i != sig:
            leak.append(f"{npc}:{i}")
check("可索要清单与自身喜好无重叠（signature 除外）", not leak, str(leak))

fox = P.get("fox_postman", {})
fox_give = set(fox.get("return_gifts") or []) | set(fox.get("mid_return_gifts") or []) \
    | set(fox.get("rare_return_gifts") or [])
if fox.get("signature_gift"):
    fox_give.add(fox["signature_gift"])
check("焰仔不再能送出火卷轴（他 loves 但并不持有）",
      "scroll_fire" not in fox_give, str(sorted(fox_give)))
check("火卷轴的真实持有者仍是小蓝",
      [h["npc_id"] for h in idx.holders("scroll_fire")] == ["traveler_lan"],
      str([h["name"] for h in idx.holders("scroll_fire")]))

print("\n=== 9. 第三条铁律：回礼池 ∩ 自己的任务需求物 = ∅ ===")
# 否则玩家可以「找他要来 → 再交还给他」完成任务，白拿奖励和好感。
# 实际抓到过两处：煊赫要萤石（萤石唯一来源就是他自己的招牌礼）、
# 老咸要章鱼（章鱼在他自己的普通回礼池里）。
_Q = json.loads((ROOT / "data/world/quests.json").read_text(encoding="utf-8"))
_Q = {k: v for k, v in _Q.items() if not k.startswith("_")}
self_supply = []
for npc, p in P.items():
    pool = set()
    for f in ("return_gifts", "mid_return_gifts", "rare_return_gifts"):
        pool |= set(p.get(f) or [])
    if p.get("signature_gift"):
        pool.add(p["signature_gift"])
    need = {
        q["requires"]["item_id"] for q in _Q.values()
        if q.get("npc_id") == npc and q.get("kind") == "collect"
        and (q.get("requires") or {}).get("item_id")
    }
    for i in sorted(need & pool):
        self_supply.append(f"{npc}:{i}")
check("没有 NPC 自产自销（要的东西自己就能给）", not self_supply, str(self_supply))

print("\n=== 10. 任务门槛必须与需求物的获取难度匹配 ===")
# 修复前的倒挂：close 档「天亮前的露水」只要玩家随手捡的露珠(v2)，
# 而 neutral 档「找一件火卷轴」却要全镇最硬的通货(v10，只有小蓝 rare 档给)。
# 前者让玩家觉得辛苦刷来的好感白费，后者让新手一见面就被卡死。
_ground = {s["item_id"] for s in json.loads(
    (ROOT / "data/world/spawners.json").read_text(encoding="utf-8"))["spawners"]}
_LV = {"neutral": 0, "friendly": 1, "fond": 2, "close": 3, "intimate": 4}


def _tier(iid):
    bv = items_module._ITEMS[iid].base_value
    if iid in _ground or bv <= 3:
        return 0          # 地图可捡
    return 2 if bv <= 9 else 3   # NPC 中档 / 稀有


_OK = {0: (0, 1), 2: (1, 2), 3: (3, 4)}
_LABEL = {0: "地图可捡→neutral/friendly", 2: "NPC中档→friendly/fond",
          3: "NPC稀有→close/intimate"}
mismatch = []
for k, q in _Q.items():
    if q.get("kind") != "collect":
        continue
    iid = (q.get("requires") or {}).get("item_id")
    if not iid or iid not in items_module._ITEMS:
        continue
    t = _tier(iid)
    lo, hi = _OK[t]
    cur = _LV.get(q.get("min_affection_level", "neutral"), 0)
    if not (lo <= cur <= hi):
        mismatch.append(
            f"{q['title']}(要{items_module._ITEMS[iid].name}v{items_module._ITEMS[iid].base_value},"
            f"门槛{q.get('min_affection_level')},应{_LABEL[t]})")
check("无门槛倒挂的 collect 任务", not mismatch,
      f"{len(mismatch)} 个: {mismatch[:3]}" if mismatch else "")

# 顺带确认重排没把任务弄成不可达
unreachable = []
for k, q in _Q.items():
    if q.get("kind") != "collect":
        continue
    iid = (q.get("requires") or {}).get("item_id")
    if not iid or iid not in items_module._ITEMS:
        continue
    if iid not in _ground and items_module._ITEMS[iid].base_value > 3 \
            and not idx.holders(iid):
        unreachable.append(f"{q['title']}:{iid}")
check("所有 collect 需求物仍然可达（无死锁）", not unreachable, str(unreachable))

print(f"\n=== 结果汇总 ===\n通过 {sum(results)}/{len(results)}")
sys.exit(0 if all(results) else 1)
