#!/usr/bin/env python3
"""送礼公式测试：偏好档位 / 疲劳 / 负向礼物不得被疲劳翻正。

运行: ./agent_server/.venv/bin/python tests/test_gift_formula.py

背景：玩家问「不喜欢的礼物收到会加分吗？讨厌的会扣分吗？」
排查时发现一个刷分漏洞——疲劳系数会降到 -0.5，而
负 pref_mult × 负 fatigue = 正，于是连送 4 次对方最讨厌的东西
就开始加分（-12 → -7 → -1 → +4）。本测试锁死该行为。
"""

import sys
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


import gifts  # noqa: E402
import items  # noqa: E402

# 挑不同 base_value 的物品，覆盖「便宜的讨厌物」与「昂贵的讨厌物」
PREFS = {
    "loves":    ["fish"],        # v=1
    "likes":    ["bread"],       # v=2
    "dislikes": ["gold_cup"],    # v=12
    "hates":    ["gem_red"],     # v=12
}

print("=== 1. 四档偏好的方向是否符合设计 ===")
d_love = gifts.compute_delta("fish", PREFS, "neutral", 0)
d_like = gifts.compute_delta("bread", PREFS, "neutral", 0)
d_neu = gifts.compute_delta("mushroom", PREFS, "neutral", 0)
d_dis = gifts.compute_delta("gold_cup", PREFS, "neutral", 0)
d_hate = gifts.compute_delta("gem_red", PREFS, "neutral", 0)

check("loves 加分", d_love["delta"] > 0, str(d_love["delta"]))
check("likes 加分", d_like["delta"] > 0, str(d_like["delta"]))
check("neutral 加分", d_neu["delta"] > 0, str(d_neu["delta"]))
check("dislikes 仍是正向（只是打三折，不是扣分）",
      gifts.PREF_MULT["dislikes"] > 0 and d_dis["delta"] >= 0,
      f"金杯v12 → {d_dis['delta']}")
check("hates 扣分", d_hate["delta"] < 0, f"红宝石v12 → {d_hate['delta']}")
check("越贵的讨厌物扣得越狠",
      gifts.compute_delta("gem_red", PREFS, "neutral", 0)["delta"]
      < gifts.compute_delta("feather", {"hates": ["feather"]}, "neutral", 0)["delta"],
      "v12 vs v1")

print("\n=== 2. 讨厌的东西反复送，永远不该变成加分 ===")
seq = [gifts.compute_delta("gem_red", PREFS, "neutral", c)["delta"] for c in range(10)]
print(f"     连送 10 次的 delta 序列: {seq}")
check("全程没有一次是正数（修复前第 4 次会变 +4）",
      all(d <= 0 for d in seq), str(seq))
check("惩罚随疲劳减弱但不反转（单调不减且封顶 0）",
      all(seq[i] <= seq[i + 1] for i in range(len(seq) - 1)) and max(seq) == 0,
      str(seq))
check("负向疲劳系数被钳到 0",
      gifts.effective_fatigue_mult(-0.5, gifts.PREF_MULT["hates"]) == 0.0)

print("\n=== 3. 正向礼物的疲劳机制不受影响 ===")
pos = [gifts.compute_delta("fish", PREFS, "neutral", c)["delta"] for c in range(6)]
print(f"     连送 10 次喜爱物的 delta 序列: {pos}")
check("正向疲劳仍会一路衰减（送多了不新鲜）", pos[0] > pos[-1], str(pos))
check("正向送太多最终会反感（可以变负）", min(pos) < 0, str(pos))
check("正向疲劳系数不被钳",
      gifts.effective_fatigue_mult(-0.5, gifts.PREF_MULT["loves"]) == -0.5)

print("\n=== 4. 增益封顶只作用于正向 ===")
big = gifts.compute_delta("gem_red", {"loves": ["gem_red"]}, "fond", 0)
check("正向封顶 GIFT_DELTA_CAP",
      big["delta"] <= gifts.GIFT_DELTA_CAP, str(big["delta"]))
check("负向不封顶（保留惩罚力度）",
      d_hate["delta"] < -gifts.GIFT_DELTA_CAP, str(d_hate["delta"]))

print("\n=== 5. 文档注释与实际常量一致（防止再次漂移）===")
src = (ROOT / "agent_server" / "gifts.py").read_text(encoding="utf-8")
head = src.split('"""')[1]
for k, v in gifts.PREF_MULT.items():
    check(f"注释里 {k} 的数值是 {v}", f"{k}" in head and str(v) in head)
check("注释用的是 6 档新名（不再有旧的 cold/like/love 档）",
      "hostile" in head and "intimate" in head and "cold ×" not in head)

print("\n=== 结果汇总 ===")
ok = sum(1 for r in results if r)
print(f"通过 {ok}/{len(results)}")
sys.exit(0 if ok == len(results) else 1)
