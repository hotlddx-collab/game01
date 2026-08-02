#!/usr/bin/env python3
"""好感 6 档阈值 + 换届衰减曲线测试。

运行: ./agent_server/.venv/bin/python tests/test_affection_decay.py
覆盖：
1. 6 档阈值边界（每档上下沿都取到）
2. 档位数 == 名字板颜色数，且 at_least 单调
3. 换届衰减只削 FLOOR 以上部分，FLOOR 以下不动
4. 反复换届不会跌破 FLOOR（底子保得住）
5. 敌对关系不会被换届洗白
"""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "agent_server"))

PASS = "\033[92m✅ PASS\033[0m"
FAIL = "\033[91m❌ FAIL\033[0m"

results = []


def check(name, condition, detail=""):
    ok = bool(condition)
    sym = PASS if ok else FAIL
    print(f"{sym}  {name}" + (f"  ({detail})" if detail else ""))
    results.append(ok)


TEST_DB = Path(tempfile.mkdtemp()) / "test_affection_decay.db"
os.environ["TOWN_DB_PATH"] = str(TEST_DB)

import db  # noqa: E402
db.current_db_path.set(TEST_DB)
db.init_schema(TEST_DB)

from affection import (  # noqa: E402
    AffectionStore, level_of, level_label, at_least, LEVEL_ORDER,
    greet_gain, VALUE_MIN, VALUE_MAX,
)
from election import (  # noqa: E402
    ElectionStore, AFFECTION_DECAY_FLOOR, AFFECTION_DECAY_RATE,
)
from world_events import WorldEventStore  # noqa: E402

NPCS = ["bear_baker", "fox_postman", "herbalist_cui",
        "mystic_xuan", "pirate_lao", "traveler_lan"]

print("=== 1. 6 档阈值边界 ===")

# (value, 期望档位)：每档取下沿、下沿-1，确保边界不飘
CASES = [
    (VALUE_MIN, "hostile"), (-1, "hostile"),
    (0, "neutral"), (14, "neutral"),
    (15, "friendly"), (34, "friendly"),
    (35, "fond"), (59, "fond"),
    (60, "close"), (84, "close"),
    (85, "intimate"), (VALUE_MAX, "intimate"),
]
bad = [(v, level_of(v), want) for v, want in CASES if level_of(v) != want]
check("所有档位边界正确", not bad, str(bad))

print("\n=== 2. 档位数与颜色数一致 ===")

check("恰好 6 档", len(LEVEL_ORDER) == 6, str(sorted(LEVEL_ORDER, key=LEVEL_ORDER.get)))

# 名字板颜色表由 animal.gd 维护，这里做纯文本核对，避免两边悄悄脱节
animal_gd = (ROOT / "scripts" / "animal.gd").read_text(encoding="utf-8")
missing = [lv for lv in LEVEL_ORDER if f'"{lv}"' not in animal_gd]
check("每档都在 animal.gd 颜色表里有对应颜色", not missing, str(missing))

check("每档都有中文标签",
      all(level_label(lv) != "普通" or lv == "neutral" for lv in LEVEL_ORDER))

# at_least 必须与数值序一致
mono = all(
    at_least(level_of(hi), level_of(lo))
    for lo in range(VALUE_MIN, VALUE_MAX, 7)
    for hi in range(lo, VALUE_MAX, 13)
)
check("at_least 与数值大小单调一致", mono)

print("\n=== 3. 换届衰减：只削 FLOOR 以上 ===")

aff = AffectionStore()
ws = WorldEventStore()
es = ElectionStore(NPCS, aff, ws)


def expected(v):
    if v <= AFFECTION_DECAY_FLOOR:
        return v
    return int(round(AFFECTION_DECAY_FLOOR
                     + (v - AFFECTION_DECAY_FLOOR) * (1.0 - AFFECTION_DECAY_RATE)))


setup = {
    "bear_baker": 100,   # 顶档，应跌出 intimate
    "fox_postman": 60,   # close 下沿
    "herbalist_cui": 35,  # 正好 FLOOR，不动
    "mystic_xuan": 20,   # FLOOR 以下，不动
    "pirate_lao": 0,
    "traveler_lan": -30,  # 敌对，不该被洗白
}
for npc, v in setup.items():
    aff.adjust(npc, v)
check("初始值写入成功",
      all(aff.get(n) == v for n, v in setup.items()))

decayed = es.decay_affection_on_term_end()
after = {n: aff.get(n) for n in setup}

wrong = [(n, setup[n], after[n], expected(setup[n]))
         for n in setup if after[n] != expected(setup[n])]
check("衰减结果与公式一致", not wrong, str(wrong))

check("满值 100 跌出顶档",
      not at_least(level_of(after["bear_baker"]), "intimate"),
      f"100 → {after['bear_baker']} ({level_of(after['bear_baker'])})")

check("FLOOR 以下不动",
      after["mystic_xuan"] == 20 and after["pirate_lao"] == 0)

check("敌对关系不被换届洗白",
      after["traveler_lan"] == -30,
      f"{after['traveler_lan']} ({level_of(after['traveler_lan'])})")

check("只对实际变化的 NPC 返回记录",
      {d["npc_id"] for d in decayed} == {"bear_baker", "fox_postman"},
      str([d["npc_id"] for d in decayed]))

print("\n=== 4. 反复换届不跌破 FLOOR ===")

aff.adjust("bear_baker", 100 - aff.get("bear_baker"))  # 拉回满值
trace = [aff.get("bear_baker")]
for _ in range(10):
    es.decay_affection_on_term_end()
    trace.append(aff.get("bear_baker"))
check("满值连续 10 届衰减始终 >= FLOOR",
      all(v >= AFFECTION_DECAY_FLOOR for v in trace), str(trace))
check("衰减单调不反弹", all(a >= b for a, b in zip(trace, trace[1:])), str(trace))

print("\n=== 5. 顶档必须每届重争 ===")
check("一届衰减后顶档需再挣 >= 10 点才能回去",
      expected(VALUE_MAX) <= VALUE_MAX - 10,
      f"{VALUE_MAX} → {expected(VALUE_MAX)}")

print("\n=== 6. 打招呼白嫖已堵住 ===")

aff.adjust("pirate_lao", -aff.get("pirate_lao"))  # 归零
check("重置为 0", aff.get("pirate_lao") == 0)

# 连续 60 天只打招呼，什么都不做
for day in range(1, 61):
    aff.adjust_for_greet("pirate_lao", day)
greet_only = aff.get("pirate_lao")
lvl = level_of(greet_only)
check("纯寒暄 60 天到不了顶档",
      not at_least(lvl, "intimate"), f"60 天 → {greet_only} ({lvl})")
check("纯寒暄 60 天到不了好友档",
      not at_least(lvl, "close"), f"{greet_only} ({lvl})")
check("纯寒暄仍能自然升到友善档（不至于毫无反馈）",
      at_least(lvl, "friendly"), f"{greet_only} ({lvl})")

# 同一天重复打招呼不能叠加
before = aff.get("pirate_lao")
for _ in range(10):
    aff.adjust_for_greet("pirate_lao", 61)
check("同一游戏日重复打招呼不叠加",
      aff.get("pirate_lao") - before <= 1,
      f"{before} → {aff.get('pirate_lao')}")

# 高档位下寒暄增益必须显著低于低档位
low = sum(greet_gain(10, d) for d in range(1, 21))
high = sum(greet_gain(90, d) for d in range(1, 21))
check("高档寒暄增益显著低于低档", high * 2 <= low, f"低档 {low} vs 高档 {high}")

print("\n=== 结果汇总 ===")
print(f"通过 {sum(results)}/{len(results)}")
sys.exit(0 if all(results) else 1)
