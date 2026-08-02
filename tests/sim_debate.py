#!/usr/bin/env python3
"""辩论策略性验证：确认不存在「闭眼选某一象限」的常数最优解。

跑法：python3 tests/sim_debate.py
"""
import json
import itertools
from pathlib import Path

ROOT = Path(__file__).parent.parent
DATA = json.load(open(ROOT / "data" / "world" / "debate_questions.json", encoding="utf-8"))

STANCES = DATA["_stances"]
TS = DATA["npc_topic_stance"]
SAL = DATA["npc_topic_salience"]
NPCS = list(DATA["npc_stance_pref"])
TOPICS = list(DATA["_topic_labels"])


def payoff(stance, topic, voters):
    """玩家站 stance 在 topic 上能拿到的加权票。"""
    return sum(SAL[v][topic] for v in voters if TS[v][topic] == stance)


def main():
    print("=== 1. 每题最优象限是否随议题变化 ===")
    best_per_topic = {}
    for t in TOPICS:
        scores = {s: payoff(s, t, NPCS) for s in STANCES}
        best = max(scores, key=scores.get)
        best_per_topic[t] = best
        print(f"  {DATA['_topic_labels'][t]:8s} 最优={best:12s} "
              + " ".join(f"{s}={scores[s]:.1f}" for s in STANCES))
    distinct = len(set(best_per_topic.values()))
    print(f"  → 最优象限种类数 = {distinct}（>1 即无全局常数解）")

    print("\n=== 2. 固定单一象限 vs 逐题最优 ===")
    fixed = {}
    for s in STANCES:
        fixed[s] = sum(payoff(s, t, NPCS) for t in TOPICS)
    adaptive = sum(payoff(best_per_topic[t], t, NPCS) for t in TOPICS)
    for s in STANCES:
        print(f"  全程 {s:12s} = {fixed[s]:.1f}")
    print(f"  逐题最优       = {adaptive:.1f}")
    gap = adaptive - max(fixed.values())
    print(f"  → 领先最佳固定策略 {gap:.1f}（>0 说明动脑有回报）")

    print("\n=== 3. 通吃检验：最优选择也一定得罪人 ===")
    opp = {"radical": "conservative", "conservative": "radical",
           "pleasing": "pragmatic", "pragmatic": "pleasing"}
    for t in TOPICS:
        b = best_per_topic[t]
        hurt = [v for v in NPCS if TS[v][t] == opp[b] and SAL[v][t] >= 1.0]
        print(f"  {DATA['_topic_labels'][t]:8s} 选{b:12s} 得罪 {hurt or '无'}")

    print("\n=== 结论 ===")
    ok1 = distinct > 1
    ok2 = gap > 0
    print(f"  无常数最优解: {'✅' if ok1 else '❌'}")
    print(f"  自适应有收益: {'✅' if ok2 else '❌'}")


if __name__ == "__main__":
    main()
