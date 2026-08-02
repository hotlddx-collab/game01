#!/usr/bin/env python3
"""对手强度曲线 + 挖墙脚测试。

运行: ./agent_server/.venv/bin/python tests/test_opponent_strength.py
覆盖：
1. 难度系数：term1-3 保持原阶梯，term4 起持续爬升且封顶
2. 挖墙脚目标：挑玩家好感最高的那位，低好感不下手
3. 挖墙脚生效：真的扣好感，且写入 mechanical_effect 供前端展示
4. term1 不挖墙脚（新手局）
"""

import os
import sys
import asyncio
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


TEST_DB = Path(tempfile.mkdtemp()) / "test_opp.db"
os.environ["TOWN_DB_PATH"] = str(TEST_DB)

import db  # noqa: E402
db.current_db_path.set(TEST_DB)
db.init_schema(TEST_DB)

from election import (  # noqa: E402
    ElectionStore, TERM_DIFFICULTY, TERM_DIFFICULTY_MAX,
    TERM_DIFFICULTY_GROWTH, TERM_DIFFICULTY_DEFAULT,
)
from affection import AffectionStore  # noqa: E402
from world_events import WorldEventStore  # noqa: E402
import opponent_ai as oa  # noqa: E402

NPCS = ["bear_baker", "fox_postman", "herbalist_cui",
        "mystic_xuan", "pirate_lao", "traveler_lan"]

print("=== 1. 难度曲线：term4 起不再封顶 ===")
f = ElectionStore._term_factor
check("term1 = 0.5", f({"term_id": 1}) == 0.5)
check("term3 = 0.85", f({"term_id": 3}) == 0.85)
check("term4 = 1.0（原封顶值）", abs(f({"term_id": 4}) - 1.0) < 1e-6, str(f({"term_id": 4})))

prev = f({"term_id": 4})
rising = True
for t in range(5, 12):
    cur = f({"term_id": t})
    if cur < prev:
        rising = False
    prev = cur
check("term5+ 持续不降（修复前恒为 1.0）", rising)
check("term5 已高于旧封顶", f({"term_id": 5}) > 1.0, str(f({"term_id": 5})))
check("难度有天花板，不会无限膨胀",
      f({"term_id": 99}) == TERM_DIFFICULTY_MAX, str(f({"term_id": 99})))
check("增长步长符合定义",
      abs(f({"term_id": 5}) - (TERM_DIFFICULTY_DEFAULT + TERM_DIFFICULTY_GROWTH)) < 1e-6,
      str(f({"term_id": 5})))
check("term4→5 无断层（term4 仍是旧满难度）",
      f({"term_id": 4}) == TERM_DIFFICULTY_DEFAULT)

print("\n=== 2. 挖墙脚目标选择 ===")
aff = AffectionStore()
world = WorldEventStore()
es = ElectionStore(npc_ids=NPCS, affection_store=aff, world_store=world)
personas = {n: {"name": n, "species": "怪物"} for n in NPCS}
ai = oa.OpponentAI(election_store=es, personas=personas, llm=None,
                   world_store=world, affection_store=aff)

opp = "fox_postman"
aff.adjust("bear_baker", 88)      # 玩家的铁票
aff.adjust("pirate_lao", 70)
aff.adjust("herbalist_cui", 20)   # 好感不够，不该被挖

t2 = {"term_id": 2, "opponent_id": opp}
target = ai._pick_poach_target(t2, opp)
check("挑好感最高的下手", target == "bear_baker", target)

t1 = {"term_id": 1, "opponent_id": opp}
check("term1 新手局不挖墙脚", ai._pick_poach_target(t1, opp) == "",
      ai._pick_poach_target(t1, opp))

# 全员低好感时不该硬挖
aff2_db = Path(tempfile.mkdtemp()) / "low.db"
db.current_db_path.set(aff2_db)
db.init_schema(aff2_db)
aff_low = AffectionStore()
es_low = ElectionStore(npc_ids=NPCS, affection_store=aff_low, world_store=WorldEventStore())
ai_low = oa.OpponentAI(election_store=es_low, personas=personas, llm=None,
                       world_store=WorldEventStore(), affection_store=aff_low)
for n in NPCS:
    aff_low.adjust(n, 10)
check("全员好感偏低时不挖（挖了也没手感）",
      ai_low._pick_poach_target(t2, opp) == "", ai_low._pick_poach_target(t2, opp))

print("\n=== 3. 挖墙脚真的扣好感 ===")
db.current_db_path.set(TEST_DB)
before = int(aff.get("bear_baker"))


class _StubLLM:
    async def chat(self, **kw):
        raise RuntimeError("离线测试不调 LLM")


ai.llm = _StubLLM()
action = asyncio.get_event_loop().run_until_complete(
    ai._execute_action(2, 5, opp, "bear_baker", oa.ACTION_POACH, ["纲领"])
)
after = int(aff.get("bear_baker"))
check("好感被拉低", after < before, f"{before} → {after}")
check("扣减幅度符合定义", after - before == oa.POACH_AFFECTION_DELTA,
      str(after - before))
check("effect 带 affection_delta 供前端展示",
      action["mechanical_effect"].get("affection_delta") == oa.POACH_AFFECTION_DELTA,
      str(action["mechanical_effect"]))
check("LLM 挂了也有兜底台词", action["llm_text"] in oa.FALLBACK_POACH_TEXTS,
      action["llm_text"])

print("\n=== 4. 其余动作不该动好感 ===")
b2 = int(aff.get("pirate_lao"))
asyncio.get_event_loop().run_until_complete(
    ai._execute_action(2, 5, opp, "pirate_lao", oa.ACTION_SMEAR, ["纲领"])
)
check("smear 不直接改好感（只影响选票）", int(aff.get("pirate_lao")) == b2,
      f"{b2} → {int(aff.get('pirate_lao'))}")

print("\n=== 5. 目标分派里包含挖墙脚 ===")
picks = ai._pick_targets_by_score(t2, opp, 6)
kinds = {v: k for v, k in picks}
check("玩家铁票被分派为 poach",
      kinds.get("bear_baker") == oa.ACTION_POACH, str(picks))
check("不会全员都挖（只挖最铁那个）",
      sum(1 for k in kinds.values() if k == oa.ACTION_POACH) == 1, str(kinds))

print("\n=== 结果汇总 ===")
ok = sum(1 for r in results if r)
print(f"通过 {ok}/{len(results)}")
sys.exit(0 if ok == len(results) else 1)
