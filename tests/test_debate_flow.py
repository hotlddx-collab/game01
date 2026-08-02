#!/usr/bin/env python3
"""辩论后端端到端冒烟：抽题 → 站位可见性 → 计分 → 掉好感。

跑法: python3 tests/test_debate_flow.py
"""
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

from debate import DebateManager, STANCES  # noqa: E402
from affection import AffectionStore  # noqa: E402
from election import ElectionStore  # noqa: E402
from world_events import WorldEventStore  # noqa: E402

ANIMALS = ["fox_postman", "bear_baker", "herbalist_cui",
           "pirate_lao", "mystic_xuan", "traveler_lan"]
personas = {a: {"name": a} for a in ANIMALS}

aff = AffectionStore()
election = ElectionStore(ANIMALS, aff, WorldEventStore())
term = election.ensure_term_active(1)
dm = DebateManager(election, personas, llm=None, affection_store=aff)

print("=== 1. 站位随议题变化 ===")
seen = {}
for t in dm.topic_labels:
    seen[t] = tuple(sorted(dm.stance_on(n, t) for n in ANIMALS))
check("不同议题的站位分布不同", len(set(seen.values())) > 1,
      f"{len(set(seen.values()))} 种分布")

print("\n=== 2. 抽题带 topic 与按题 camps ===")
qs = dm.pick_questions(term, n=3, session=0)
check("抽到 3 题", len(qs) == 3)
check("每题带 topic", all(q.get("topic") for q in qs))
check("每题带 camps", all("camps" in q for q in qs))

print("\n=== 3. 好感不足 → 立场不明 ===")
topic = qs[0]["topic"]
unknown = [c for c in qs[0]["camps"] if c["stance"] == "unknown"]
check("低好感时存在立场不明的镇民", len(unknown) == 1 and unknown[0]["npcs"],
      f"{len(unknown[0]['npcs']) if unknown else 0} 人未知")

print("\n=== 4. 打听后解锁站位 ===")
target = ANIMALS[0]
check("打听前未知", not dm.knows_stance(target, topic))
dm.record_intel(target, topic)
check("打听后已知", dm.knows_stance(target, topic))

print("\n=== 5. 好感够高也解锁 ===")
aff.adjust(ANIMALS[1], 40)
check("好感 40 → 可见", dm.knows_stance(ANIMALS[1], "budget"))

print("\n=== 6. 计分带 salience 权重 ===")
answers = {i: "pragmatic" for i in range(len(qs))}
topics = {i: qs[i]["topic"] for i in range(len(qs))}
res = dm.score_and_persist(term, answers, topics)
check("产出 player_scores", len(res["player_scores"]) > 0)
check("分数非全等（在意度造成差异）",
      len(set(round(v, 3) for v in res["player_scores"].values())) > 1,
      str({k: round(v, 2) for k, v in res["player_scores"].items()}))

print("\n=== 7. 站对立象限掉好感 ===")
before = {a: aff.get(a) for a in ANIMALS}
term2 = term
# 全选 pleasing，其对立面 pragmatic 阵营会记仇
res2 = dm.score_and_persist(term2, {i: "pleasing" for i in range(len(qs))}, topics)
after = {a: aff.get(a) for a in ANIMALS}
dropped = [a for a in ANIMALS if after[a] < before[a]]
check("有人因立场对立掉好感", len(dropped) > 0, f"{dropped}")

print("\n=== 8. 同议题横跳才罚 ===")
c_cross = dm.consistency_factor(["radical", "pleasing", "pragmatic"],
                                ["order", "welfare", "budget"])
c_flip = dm.consistency_factor(["radical", "pleasing", "pragmatic"],
                               ["order", "order", "order"])
check("跨议题换立场不罚", abs(c_cross - 1.0) < 1e-6, f"{c_cross:.2f}")
check("同议题横跳被罚", c_flip < 0.99, f"{c_flip:.2f}")

print("\n=== 9. 对手 AI 不再是固定交替 ===")
picks = [dm.opponent_choice(term, i, [], t) for i, t in enumerate(list(dm.topic_labels))]
check("对手站位随议题变化", len(set(picks)) > 1, str(picks))

print(f"\n=== 结果汇总 ===\n通过 {sum(results)}/{len(results)}")
sys.exit(0 if all(results) else 1)
