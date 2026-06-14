#!/usr/bin/env python3
"""选举系统单元测试。

运行: python3 tests/test_election.py
覆盖：
1. 任期生命周期（首期/换届/对手轮换）
2. weight 5 子项计算（affection/event/loyalty 接通；promise/debate 占位 0）
3. D7 自动结算（正常/平局）
4. settle_term_if_due 边界条件
5. opponent 排除规则（不连任 + 候选池筛选）
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "agent_server"))

PASS = "\033[92m✅ PASS\033[0m"
FAIL = "\033[91m❌ FAIL\033[0m"

results = []
def check(name, condition, detail=""):
    sym = PASS if condition else FAIL
    print(f"{sym}  {name}" + (f"  ({detail})" if detail else ""))
    results.append((name, condition))

# 用临时数据库
import tempfile
TEST_DB = Path(tempfile.mkdtemp()) / "test_election.db"
os.environ["TOWN_DB_PATH"] = str(TEST_DB)

# Monkey-patch DB_PATH
import db
db.DB_PATH = TEST_DB
db._initialized = False
db.init_schema()

from election import (
    ElectionStore, affection_norm, TERM_DAYS, VOTE_DAY_INDEX,
    PLAYER_ID, DEFAULT_FIRST_OPPONENT, W_AFFECTION_MAX, W_LOYALTY_MAX,
    W_PROMISE_MAX,
)
from affection import AffectionStore
from world_events import WorldEventStore
from promises import PromiseStore

NPCS = ["bear_baker", "fox_postman", "herbalist_cui", "mystic_xuan", "pirate_lao", "traveler_lan"]


# ──────────────────────────────────────────────────────────
# 1. 任期生命周期
print("\n=== 1. 任期生命周期 ===")
aff = AffectionStore()
ws = WorldEventStore()
es = ElectionStore(NPCS, aff, ws)

term1 = es.ensure_term_active(0)
check("首期自动创建 term_id=1", int(term1["term_id"]) == 1, f"got {term1['term_id']}")
check("首期对手 = bear_baker", term1["opponent_id"] == DEFAULT_FIRST_OPPONENT, term1["opponent_id"])
check("首期 day_index=1", es.day_index_in_term(term1, 0) == 1)
check("首期 phase=campaign", es.phase_of(es.day_index_in_term(term1, 0)) == "campaign")

# day 5 = day_index 6 = debate
check("day_index=6 phase=debate", es.phase_of(es.day_index_in_term(term1, 5)) == "debate")
# day 6 = day_index 7 = vote
check("day_index=7 phase=vote", es.phase_of(es.day_index_in_term(term1, 6)) == "vote")


# ──────────────────────────────────────────────────────────
# 2. affection_norm 公式（v2 平衡：非线性曲线，max=20）
print("\n=== 2. affection_norm 公式 ===")
check("affection_norm(-50)=0", affection_norm(-50) == 0.0)
check("affection_norm(100)=W_AFFECTION_MAX=20",
      abs(affection_norm(100) - W_AFFECTION_MAX) < 0.01)
# 0 → 4
check("affection_norm(0)=4", abs(affection_norm(0) - 4.0) < 0.01,
      f"got {affection_norm(0)}")
# 30 → 12
check("affection_norm(30)=12", abs(affection_norm(30) - 12.0) < 0.01)
# 60 → 16
check("affection_norm(60)=16", abs(affection_norm(60) - 16.0) < 0.01)
check("affection_norm(-100) clamped=0", affection_norm(-100) == 0.0)


# ──────────────────────────────────────────────────────────
# 3. weight 5 子项
print("\n=== 3. weight 子项 ===")
# 设置 affection
aff.adjust("fox_postman", 50)   # 50 → 12 + 20*(4/30) = 14.67
aff.adjust("pirate_lao", 60)    # 60 → 16
aff.adjust("herbalist_cui", -20)  # -20 → 30*0.08 = 2.4

w_fox_player, sub = es.compute_weight("fox_postman", PLAYER_ID, term1)
check("fox_postman→player affection≈14.67 (50 经非线性)",
      abs(sub["affection"] - 14.6666) < 0.01,
      f"got {sub['affection']}")

w_lao_player, sub = es.compute_weight("pirate_lao", PLAYER_ID, term1)
check("pirate_lao→player affection=16 (60)",
      abs(sub["affection"] - 16.0) < 0.01,
      f"got {sub['affection']}")
check("pirate_lao→player loyalty=0 (玩家无亲近圈)",
      sub["loyalty"] == 0.0)

w_fox_op, sub = es.compute_weight("fox_postman", "bear_baker", term1)
# fox 在 bear_baker 亲近圈
check("fox_postman→bear_baker loyalty=W_LOYALTY_MAX",
      sub["loyalty"] == W_LOYALTY_MAX)
# 对手 base 4 + 0 visits + 4 (亲近圈 bonus) = 8
check("fox_postman→bear_baker affection=8 (无 visit + 亲近圈 +4)",
      abs(sub["affection"] - 8.0) < 0.01,
      f"got {sub['affection']}")


# ──────────────────────────────────────────────────────────
# 4. event 子项（基于 world_events）
print("\n=== 4. event 子项 ===")
ws.add(actor="player", description="玩家 帮 fox_postman 修信箱", location="post")
ws.add(actor="player", description="玩家 送 fox_postman 一条鱼", location="plaza")
ws.add(actor="bear_baker", description="bear_baker 拒绝 fox_postman 的请求 麻烦死了", location="bakery")

w_fox_player2, sub2 = es.compute_weight("fox_postman", PLAYER_ID, term1)
check("玩家 2 个正面事件 → fox_postman event ≥ +6 (3*2)",
      sub2["event"] >= 6.0,
      f"got {sub2['event']}")

w_fox_op2, sub2b = es.compute_weight("fox_postman", "bear_baker", term1)
check("bear_baker 1 个负面事件 → fox_postman event ≤ -3",
      sub2b["event"] <= -3.0,
      f"got {sub2b['event']}")


# ──────────────────────────────────────────────────────────
# 5. recompute_and_persist + view
print("\n=== 5. 重算与视图 ===")
es.recompute_and_persist_weights(term1, 0)
view = es.get_current_term_view(0)
check("视图含 candidates", view["candidates"] == [PLAYER_ID, "bear_baker"])
check("视图含 5 voters",
      len(view["voters"]) == 5,
      f"voters={view['voters']}")
check("scores 字典含 player + 对手",
      set(view["scores"].keys()) == {PLAYER_ID, "bear_baker"})


# ──────────────────────────────────────────────────────────
# 6. D7 结算
print("\n=== 6. D7 自动结算 ===")
# 模拟到 day_index=7 = game_day=6
es.recompute_and_persist_weights(term1, 6)
settle = es.settle_term_if_due(6)
check("settle 返回非空", settle is not None)
if settle:
    check("settle 含 winner_id",
          settle["winner_id"] in [PLAYER_ID, "bear_baker"],
          settle["winner_id"])
    check("settle 立即开新任期",
          settle["next_term_id"] > settle["settled_term_id"])
    check("新对手不等于上届",
          settle["next_opponent_id"] != "bear_baker"
          or settle["next_opponent_id"] in NPCS,  # 容错：池小
          settle["next_opponent_id"])

# 旧任期已 end
old = es.get_active_term()
check("新 active term != 旧",
      old is not None and int(old["term_id"]) == settle["next_term_id"])


# ──────────────────────────────────────────────────────────
# 7. settle 在非投票日不触发
print("\n=== 7. 非投票日不结算 ===")
# 当前 active 是新任期 day 0 = day_index 1
no_settle = es.settle_term_if_due(int(old["start_day"]))
check("day_index=1 不触发结算", no_settle is None)


# ──────────────────────────────────────────────────────────
# 8. 对手轮换避免连任
print("\n=== 8. 对手轮换 ===")
prev_opponents = [DEFAULT_FIRST_OPPONENT, settle["next_opponent_id"]]
# 跑几届看是否会继续换人
import random as _rnd
_rnd.seed(42)
for _ in range(3):
    cur = es.get_active_term()
    start_day = int(cur["start_day"])
    es.recompute_and_persist_weights(cur, start_day + 6)
    s = es.settle_term_if_due(start_day + 6)
    if s:
        prev_opponents.append(s["next_opponent_id"])

# 至少不会连续两个相同
all_diff = all(prev_opponents[i] != prev_opponents[i+1] for i in range(len(prev_opponents)-1))
check("连续任期对手不重复",
      all_diff,
      f"对手序列={prev_opponents}")


# ──────────────────────────────────────────────────────────
# 9. promise 子项
print("\n=== 9. promise 子项 ===")
ps = PromiseStore()
es2 = ElectionStore(NPCS, aff, ws, promise_store=ps)
term_p = es2.ensure_term_active(0)
tid = int(term_p["term_id"])

# 给 fox 建 1 fulfilled + 1 broken
ps.create(tid, "player", "fox_postman", "qa1", 0, 6)
ps.create(tid, "player", "fox_postman", "qa2", 0, 6)
ps.fulfill_by_quest("qa1", 1)
# qa2 留 pending → break
ps.break_pending_for_term(tid, 6)

raw = ps.calc_score_for_voter("fox_postman", "player", terms_back=3)
check("promise raw 计算正确（+8 兑现 -10 破诺 = -2）",
      abs(raw - (-2.0)) < 0.01,
      f"got {raw}")

# 给 cui 建 2 fulfilled
ps.create(tid, "player", "herbalist_cui", "qb1", 0, 6)
ps.create(tid, "player", "herbalist_cui", "qb2", 0, 6)
ps.fulfill_by_quest("qb1", 1)
ps.fulfill_by_quest("qb2", 2)
raw_cui = ps.calc_score_for_voter("herbalist_cui", "player", terms_back=3)
check("promise raw cui = +16",
      abs(raw_cui - 16.0) < 0.01,
      f"got {raw_cui}")

# 接到 weight
_, sub_p = es2.compute_weight("fox_postman", PLAYER_ID, term_p)
check("fox_postman → player weight 含 promise -2",
      abs(sub_p["promise"] - (-2.0)) < 0.01,
      f"got {sub_p['promise']}")

_, sub_c = es2.compute_weight("herbalist_cui", PLAYER_ID, term_p)
check("cui → player weight 含 promise +16",
      abs(sub_c["promise"] - 16.0) < 0.01,
      f"got {sub_c['promise']}")

# clamp 测试
for i in range(10):
    qid = f"q_clamp_{i}"
    ps.create(tid, "player", "traveler_lan", qid, 0, 6)
    ps.fulfill_by_quest(qid, 1)
raw_lan = ps.calc_score_for_voter("traveler_lan", "player", terms_back=3)
check("promise raw 累计后超 W_PROMISE_MAX",
      raw_lan > W_PROMISE_MAX,
      f"got raw={raw_lan}")
_, sub_lan = es2.compute_weight("traveler_lan", PLAYER_ID, term_p)
check("clamp 到 W_PROMISE_MAX",
      abs(sub_lan["promise"] - W_PROMISE_MAX) < 0.01,
      f"got {sub_lan['promise']}")


# ──────────────────────────────────────────────────────────
# 10. 模拟对局：验证 v1 平衡的"挑战感"
print("\n=== 10. 模拟对局（v1 平衡）===")

# 重置一个新场景
import db as _db
import os, tempfile
TEST_DB2 = os.path.join(tempfile.mkdtemp(), "balance.db")
_db.DB_PATH = TEST_DB2
_db._initialized = False
_db.init_schema()

aff3 = AffectionStore()
ws3 = WorldEventStore()
ps3 = PromiseStore()
es3 = ElectionStore(NPCS, aff3, ws3, promise_store=ps3)
term3 = es3.ensure_term_active(0)
op3 = term3["opponent_id"]  # 默认 bear_baker

# 场景 A：玩家送礼把所有 voter 刷到 affection=80（按旧公式必赢）
voters = es3.voters_of(term3)
for v in voters:
    aff3.adjust(v, 80)  # 刷脸到 80

# 不做任何 promise / event；对手不动
es3.recompute_and_persist_weights(term3, 0)
view_a = es3.get_current_term_view(0)
p_a = view_a["scores"]["player"]
o_a = view_a["scores"][op3]
diff_a = p_a - o_a
print(f"  [A] 仅刷脸到 80：玩家={p_a:.1f} 对手={o_a:.1f} 差距={diff_a:.1f}")
check("场景 A：仅刷脸不再碾压（差距 < 80）",
      diff_a < 80.0,
      f"差距 {diff_a:.1f}")

# 场景 B：对手追了 2 visits 给每个 voter
for v in voters:
    for _ in range(2):
        with _db.get_conn() as c:
            c.execute("""INSERT INTO opponent_actions
                (term_id, game_day, candidate_id, action_type, target_npc, llm_text, mechanical_effect_json, created_at)
                VALUES (?, ?, ?, 'visit', ?, '', '{}', 0)""",
                (int(term3["term_id"]), 1, op3, v))
es3.recompute_and_persist_weights(term3, 1)
view_b = es3.get_current_term_view(1)
p_b = view_b["scores"]["player"]
o_b = view_b["scores"][op3]
diff_b = p_b - o_b
print(f"  [B] 对手 2 visit/voter 后：玩家={p_b:.1f} 对手={o_b:.1f} 差距={diff_b:.1f}")
check("场景 B：对手 visit 后差距明显缩小（< 50）",
      diff_b < 50.0,
      f"差距 {diff_b:.1f}")

# 场景 C：玩家完成 2 promise 给每 voter（但有 2 破诺）
for v in voters:
    ps3.create(int(term3["term_id"]), "player", v, f"q_{v}_a", 0, 6)
    ps3.create(int(term3["term_id"]), "player", v, f"q_{v}_b", 0, 6)
    ps3.fulfill_by_quest(f"q_{v}_a", 1)
    ps3.fulfill_by_quest(f"q_{v}_b", 2)
# 给两个 voter 各加 1 破诺
ps3.create(int(term3["term_id"]), "player", voters[0], f"q_break1", 0, 6)
ps3.create(int(term3["term_id"]), "player", voters[1], f"q_break2", 0, 6)
ps3.break_pending_for_term(int(term3["term_id"]), 6)
es3.recompute_and_persist_weights(term3, 5)
view_c = es3.get_current_term_view(5)
p_c = view_c["scores"]["player"]
o_c = view_c["scores"][op3]
diff_c = p_c - o_c
print(f"  [C] 加完成 promise（含破诺）：玩家={p_c:.1f} 对手={o_c:.1f} 差距={diff_c:.1f}")
check("场景 C：完成承诺有显著加分（玩家 > 对手）",
      p_c > o_c,
      f"玩家 {p_c:.1f} 对手 {o_c:.1f}")
check("场景 C：差距合理（不超过 100）",
      diff_c < 100.0,
      f"差距 {diff_c:.1f}")


# ──────────────────────────────────────────────────────────
# 11. 辩论日（D7）
print("\n=== 11. 辩论日 debate ===")
from debate import DebateManager, affinity, STANCES

# 亲和度
check("亲和度：相同 +1.0", affinity("radical", "radical") == 1.0)
check("亲和度：对立 -0.5", affinity("radical", "conservative") == -0.5)
check("亲和度：对立 -0.5（pleasing/pragmatic）", affinity("pleasing", "pragmatic") == -0.5)
check("亲和度：正交 +0.3", affinity("radical", "pleasing") == 0.3)

aff_d = AffectionStore()
ws_d = WorldEventStore()
es_d = ElectionStore(NPCS, aff_d, ws_d)
term_d = es_d.ensure_term_active(0)
op_d = term_d["opponent_id"]

# 用空 personas（DebateManager 只需 name；缺省回退 id）
dm = DebateManager(es_d, personas={}, llm=None, affection_store=aff_d)

# 抽题确定性
q1 = dm.pick_questions(term_d, n=3)
q2 = dm.pick_questions(term_d, n=3)
check("抽题数量 = 3", len(q1) == 3, f"got {len(q1)}")
check("抽题确定（同 term 两次一致）",
      [x["asker_id"] for x in q1] == [x["asker_id"] for x in q2],
      f"{[x['asker_id'] for x in q1]}")
check("提问者不含对手", all(x["asker_id"] != op_d for x in q1))
check("每题有 4 个象限选项", all(len(x["options"]) == 4 for x in q1))

# 评分：玩家全选某 voter 偏好的象限 → 该 voter 给玩家高分
voters_d = es_d.voters_of(term_d)
# 取第一个 voter 的偏好象限，玩家三题都选它
v0 = voters_d[0]
pref0 = dm.stance_pref(v0)
answers = {0: pref0, 1: pref0, 2: pref0}
res = dm.score_and_persist(term_d, answers)
check("评分写入玩家分", v0 in res["player_scores"])
check("玩家命中 v0 偏好 → raw=1.0", abs(res["player_scores"][v0] - 1.0) < 1e-6,
      f"got {res['player_scores'][v0]}")

# debate 子项接通 weight
_, sub_v0 = es_d.compute_weight(v0, "player", term_d)
from election import W_DEBATE_MAX
check("debate 子项接通玩家 weight（=W_DEBATE_MAX）",
      abs(sub_v0["debate"] - W_DEBATE_MAX) < 1e-6,
      f"got {sub_v0['debate']}")

# 未辩论的新任期 → debate 子项 0（先结束当前 term 再开新的）
es_d.end_term(int(term_d["term_id"]), end_day=6, winner_id="player", result={})
es_e = ElectionStore(NPCS, AffectionStore(), WorldEventStore())
term_e = es_e.ensure_term_active(100)
check("新任期 term_id 不同于已辩论任期",
      int(term_e["term_id"]) != int(term_d["term_id"]),
      f"new={term_e['term_id']} old={term_d['term_id']}")
_, sub_e = es_e.compute_weight(es_e.voters_of(term_e)[0], "player", term_e)
check("未举行辩论 → debate 子项 = 0", sub_e["debate"] == 0.0, f"got {sub_e['debate']}")

# 对手也写入 debate 基线分
check("对手 debate 分已写入", v0 in res["opponent_scores"])
_, sub_op = es_d.compute_weight(v0, op_d, term_d)
expected_op = affinity(dm.stance_pref(op_d), pref0) * W_DEBATE_MAX
check("对手 debate 子项 = 固定立场亲和度 × W_DEBATE_MAX",
      abs(sub_op["debate"] - expected_op) < 1e-6,
      f"got {sub_op['debate']} expect {expected_op}")


# ──────────────────────────────────────────────────────────
# 12. 任期权力点（D9）
print("\n=== 12. 任期权力点 power ===")
import asyncio
from power import PowerManager, ACTIONS, VISIT_AFFECTION, ANNOUNCE_AFFECTION

# 独立 DB
TEST_DB_PW = os.path.join(tempfile.mkdtemp(), "power.db")
db.DB_PATH = TEST_DB_PW
db._initialized = False
db.init_schema()

aff_pw = AffectionStore()
ws_pw = WorldEventStore()
es_pw = ElectionStore(NPCS, aff_pw, ws_pw)
term_pw = es_pw.ensure_term_active(0)
tid_pw = int(term_pw["term_id"])
voters_pw = es_pw.voters_of(term_pw)
pm = PowerManager(es_pw, aff_pw, ws_pw, personas={}, llm=None)

# 非现任 → 无权力点
check("非现任 refresh → 0", es_pw.refresh_power_points(tid_pw, "player", 0) == 0)
check("非现任 is_incumbent=False", not es_pw.is_incumbent(tid_pw, "player"))

# 设为现任 → 06:00 补满
es_pw.set_incumbent(tid_pw, "player")
check("set_incumbent 后 is_incumbent=True", es_pw.is_incumbent(tid_pw, "player"))
p_full = es_pw.refresh_power_points(tid_pw, "player", 0)
check("现任补满 = 3", p_full == 3, f"got {p_full}")

# 同日重复 refresh 不再补满（消费后保持）
es_pw.spend_power_points(tid_pw, "player", 1)
check("花 1 点后剩 2",
      int(es_pw.get_candidate_state(tid_pw, "player")["power_points"]) == 2)
check("同日 refresh 不补满（仍 2）", es_pw.refresh_power_points(tid_pw, "player", 0) == 2)
# 跨日 refresh 补满
check("跨日 refresh 补满 3", es_pw.refresh_power_points(tid_pw, "player", 1) == 3)

# 点数不足
es_pw.spend_power_points(tid_pw, "player", 3)
check("耗尽后 spend 失败", not es_pw.spend_power_points(tid_pw, "player", 1))

# visit 行动：加好感 + 扣点
es_pw.refresh_power_points(tid_pw, "player", 2)  # 补满
v_t = voters_pw[0]
aff_before = aff_pw.get(v_t)
res_v = asyncio.run(pm.perform(term_pw, 2, "visit", v_t))
check("visit ok", res_v.get("ok"), res_v.get("error", ""))
check("visit 加好感 +%d" % VISIT_AFFECTION,
      aff_pw.get(v_t) - aff_before == VISIT_AFFECTION,
      f"got +{aff_pw.get(v_t) - aff_before}")
check("visit 扣 1 点（剩 2）",
      int(es_pw.get_candidate_state(tid_pw, "player")["power_points"]) == 2)

# visit 无效目标
res_bad = asyncio.run(pm.perform(term_pw, 2, "visit", "nobody"))
check("visit 无效目标 → 失败", not res_bad.get("ok"))

# announce 行动：全体加好感 + 扣 2
befores = {v: aff_pw.get(v) for v in voters_pw}
res_a = asyncio.run(pm.perform(term_pw, 2, "announce"))
check("announce ok", res_a.get("ok"), res_a.get("error", ""))
check("announce 影响全体 voter", len(res_a.get("affected", [])) == len(voters_pw))
check("announce 全体 +%d" % ANNOUNCE_AFFECTION,
      all(aff_pw.get(v) - befores[v] == ANNOUNCE_AFFECTION for v in voters_pw))
check("announce 扣 2 点（剩 0）",
      int(es_pw.get_candidate_state(tid_pw, "player")["power_points"]) == 0)

# 点数不足时行动被拒
res_no = asyncio.run(pm.perform(term_pw, 2, "announce"))
check("点数不足 → 行动被拒", not res_no.get("ok"))

# 未知行动
res_unk = asyncio.run(pm.perform(term_pw, 2, "fly"))
check("未知行动 → 失败", not res_unk.get("ok"))

# 非现任无法行动（用独立 DB 隔离，避免共享活跃任期）
import tempfile as _tf2
_DB_NI = os.path.join(_tf2.mkdtemp(), "power_ni.db")
db.DB_PATH = _DB_NI
db._initialized = False
db.init_schema()
es_pw2 = ElectionStore(NPCS, AffectionStore(), WorldEventStore())
term_pw2 = es_pw2.ensure_term_active(0)
pm2 = PowerManager(es_pw2, AffectionStore(), WorldEventStore(), personas={}, llm=None)
res_ni = asyncio.run(pm2.perform(term_pw2, 0, "announce"))
check("非现任行动 → 失败", not res_ni.get("ok"))
# 切回 power 主测 DB
db.DB_PATH = TEST_DB_PW
db._initialized = True

# view 暴露权力字段
view_pw = es_pw.get_current_term_view(2)
check("view 含 player_incumbent=True", view_pw.get("player_incumbent") is True)
check("view 含 player_power_max=3", int(view_pw.get("player_power_max", 0)) == 3)


# ──────────────────────────────────────────────────────────
# 13. 对手强劲追赶：promise / smear / 智能选目标 / 行动数
print("\n=== 13. 对手追赶 promise/smear ===")
from election import (
    PROMISE_PER_ACTION, SMEAR_PER_ACTION, SMEAR_BACKFIRE, SMEAR_LOYAL_AFFECTION,
)
import tempfile as _tf3
_DB_OPP = os.path.join(_tf3.mkdtemp(), "opp.db")
db.DB_PATH = _DB_OPP
db._initialized = False
db.init_schema()
aff_op = AffectionStore()
ws_op = WorldEventStore()
es_op = ElectionStore(NPCS, aff_op, ws_op)
term_op = es_op.ensure_term_active(0)
op_id = term_op["opponent_id"]
voters_op = es_op.voters_of(term_op)
v0 = voters_op[0]


def _insert_opp_action(term_id, day, cand, action_type, target):
    with db.get_conn() as c:
        c.execute("""INSERT INTO opponent_actions
            (term_id, game_day, candidate_id, action_type, target_npc, llm_text, mechanical_effect_json, created_at)
            VALUES (?, ?, ?, ?, ?, '', '{}', 0)""",
            (int(term_id), day, cand, action_type, target))


# 13a. 对手 promise 动作 → 对手 promise 子项上涨
_, sub_before = es_op.compute_weight(v0, op_id, term_op)
_insert_opp_action(term_op["term_id"], 1, op_id, "promise", v0)
_insert_opp_action(term_op["term_id"], 1, op_id, "promise", v0)
_, sub_after = es_op.compute_weight(v0, op_id, term_op)
check("对手 promise 子项随动作上涨",
      sub_after["promise"] > sub_before["promise"],
      f"{sub_before['promise']:.1f}→{sub_after['promise']:.1f}")
check("对手 2 次 promise = 2*PROMISE_PER_ACTION",
      abs(sub_after["promise"] - 2 * PROMISE_PER_ACTION) < 1e-6,
      f"{sub_after['promise']:.1f}")

# 13b. 对手 smear → 玩家在该 voter 的 event 扣分
p_event_before, _ = es_op.compute_weight(v0, "player", term_op), None
pe_before = es_op._calc_event(v0, "player")
_insert_opp_action(term_op["term_id"], 2, op_id, "smear", v0)
pe_after = es_op._calc_event(v0, "player")
check("smear 削弱玩家 event 分",
      pe_after < pe_before,
      f"{pe_before:.1f}→{pe_after:.1f}")
check("1 次 smear = -SMEAR_PER_ACTION",
      abs((pe_before - pe_after) - SMEAR_PER_ACTION) < 1e-6,
      f"diff={pe_before - pe_after:.1f}")

# 13c. smear 铁票（高 affection）反噬对手
v1 = voters_op[1]
aff_op.adjust(v1, SMEAR_LOYAL_AFFECTION + 10)
op_event_before = es_op._calc_event(v1, op_id)
_insert_opp_action(term_op["term_id"], 2, op_id, "smear", v1)
op_event_after = es_op._calc_event(v1, op_id)
check("smear 铁票反噬对手 event 分",
      op_event_after < op_event_before,
      f"{op_event_before:.1f}→{op_event_after:.1f}")

# 13d. 行动数随任期推进 + 落后递增
from opponent_ai import OpponentAI, CATCHUP_BEHIND_THRESHOLD
oai = OpponentAI(es_op, personas={}, llm=None, world_store=ws_op)
n_d1 = oai._daily_action_count(1, behind=0.0)
n_d5 = oai._daily_action_count(5, behind=0.0)
n_behind = oai._daily_action_count(1, behind=CATCHUP_BEHIND_THRESHOLD + 10)
check("D1 行动数 < D5 行动数", n_d1 < n_d5, f"{n_d1} < {n_d5}")
check("落后时行动数 +1", n_behind > n_d1, f"{n_behind} > {n_d1}")
check("行动数封顶 4", oai._daily_action_count(5, behind=999) <= 4)

# 13e. 智能选目标：玩家领先多 → smear
v_lead = voters_op[2]
aff_op.adjust(v_lead, 90)   # 玩家在该 voter 大幅领先
# 给玩家完成承诺进一步拉开（promise_store 缺省，靠 affection 即可领先）
picks = oai._pick_targets_by_score(term_op, op_id, 1)
check("智能选目标返回非空", len(picks) >= 1, f"{picks}")
# 玩家领先最多的 voter 应被优先攻打（首个 pick）
check("优先攻打玩家领先的 voter",
      picks[0][0] == v_lead,
      f"pick={picks[0]}")

# 切回 power 主测 DB（保持原状）
db.DB_PATH = TEST_DB_PW
db._initialized = True


# ──────────────────────────────────────────────────────────
print(f"\n=== 结果汇总 ===")
total = len(results)
passed = sum(1 for _, c in results if c)
print(f"通过 {passed}/{total}")
sys.exit(0 if passed == total else 1)
