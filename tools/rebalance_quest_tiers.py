#!/usr/bin/env python3
"""按「门槛 ↔ 物品获取难度」规则重排 collect 任务的 min_affection_level。

规则（同时写进 quests.json 的头部注释与 test_item_source.py）：
    地图可捡物 (base_value<=3 或有 spawner)  → neutral / friendly
    NPC 普通/中档回礼 (v4-9)                 → friendly / fond
    NPC 稀有回礼 / 招牌礼 (v10+)             → close / intimate

修的是「倒挂」：close 档任务只要玩家随手能捡的露水，
neutral 档任务却要全镇最硬的火卷轴——前者让玩家觉得辛苦白费，
后者让新手直接卡死。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "agent_server"))
import items  # noqa: E402

QF = ROOT / "data/world/quests.json"
raw = json.loads(QF.read_text(encoding="utf-8"))
ground = {s["item_id"] for s in
          json.loads((ROOT / "data/world/spawners.json").read_text(encoding="utf-8"))["spawners"]}

LV = {"neutral": 0, "friendly": 1, "fond": 2, "close": 3, "intimate": 4}


def tier_of(iid: str) -> int:
    bv = items._ITEMS[iid].base_value
    if iid in ground or bv <= 3:
        return 0
    return 2 if bv <= 9 else 3


# 目标门槛：地图可捡→neutral；中档→fond；稀有→close
TARGET = {0: "neutral", 2: "fond", 3: "close"}
# 已经落在合理区间内的就别动，避免把精心调过的门槛一把抹平
OKRANGE = {0: (0, 1), 2: (1, 2), 3: (3, 4)}

changed = []
for k, q in raw.items():
    if k.startswith("_") or q.get("kind") != "collect":
        continue
    iid = (q.get("requires") or {}).get("item_id")
    if not iid or iid not in items._ITEMS:
        continue
    t = tier_of(iid)
    cur = q.get("min_affection_level", "neutral")
    lo, hi = OKRANGE[t]
    if lo <= LV.get(cur, 0) <= hi:
        continue
    q["min_affection_level"] = TARGET[t]
    changed.append((k, q["title"], items._ITEMS[iid].name,
                    items._ITEMS[iid].base_value, cur, TARGET[t]))

QF.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"重排 {len(changed)} 个任务门槛：")
for c in changed:
    print(f"  {c[1]:22} 要{c[2]}(v{c[3]:<2})  {c[4]} → {c[5]}")
