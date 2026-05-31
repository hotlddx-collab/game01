#!/usr/bin/env python3
"""
NPC 状态机自动测试
运行: python3 tests/test_npc_behavior.py

测试范围：
1. 日程解析（时间→地点映射正确）
2. PathNetwork A* 路径规划
3. 状态机时序（WAITING→TRAVELING→SETTLING→IDLE→WANDERING）
4. 超时逃脱（WANDERING 死锁保护）
5. 各 NPC 有效 home location 注册
"""

import sys, json, math, time

PASS = "\033[92m✅ PASS\033[0m"
FAIL = "\033[91m❌ FAIL\033[0m"
INFO = "\033[94mℹ️ \033[0m"

results = []

def check(name, condition, detail=""):
    sym = PASS if condition else FAIL
    print(f"{sym}  {name}" + (f"  ({detail})" if detail else ""))
    results.append((name, condition))

# ──────────────────────────────────────────────────────────
# 1. 日程解析
# ──────────────────────────────────────────────────────────
print("\n── 1. 日程解析 ──────────────────────────────────")

BASE = "data/animals/"
NPCS = ["bear_baker","fox_postman","herbalist_cui","traveler_lan","pirate_lao","mystic_xuan"]

for npc_id in NPCS:
    try:
        d = json.load(open(f"{BASE}{npc_id}.json"))
        sched = d.get("schedule", [])
        check(f"{d['name']} schedule 非空", len(sched) > 0, f"{len(sched)} 条")

        # 每条必须有 time 和 location
        for e in sched:
            ok = "time" in e and "location" in e
            if not ok:
                check(f"  {d['name']} 日程条目完整", False, str(e))

        # home_X 必须存在（不能还在用别人家）
        owns_home = any(e["location"] == f"home_{npc_id.split('_')[0]}" or
                        "home_" + npc_id.split("_")[0] in e["location"]
                        for e in sched)
        # 特殊映射
        home_map = {
            "bear_baker": "home_bear",
            "fox_postman": "home_fox",
            "herbalist_cui": "home_cui",
            "traveler_lan": "home_lan",
            "pirate_lao": "home_pirate",
            "mystic_xuan": "home_mystic",
        }
        home_id = home_map[npc_id]
        has_own_home = any(e["location"] == home_id for e in sched)
        check(f"{d['name']} 有自己的 home({home_id})", has_own_home)

        # movement 字段
        mv = d.get("movement", {})
        check(f"{d['name']} movement 参数完整",
              all(k in mv for k in ["speed_factor","restless","wander_radius"]))

    except Exception as e:
        check(f"{npc_id} JSON 读取", False, str(e))

# ──────────────────────────────────────────────────────────
# 2. PathNetwork 路网完整性
# ──────────────────────────────────────────────────────────
print("\n── 2. PathNetwork 路网 ────────────────────────")

try:
    pn = json.load(open("data/world/path_network.json"))
    wps = pn["waypoints"]
    conns = pn["connections"]

    check("路网节点 ≥ 10", len(wps) >= 10, f"{len(wps)} 个")
    check("路网连线 ≥ 10", len(conns) >= 10, f"{len(conns)} 条")

    # 所有连线的节点必须存在
    bad_conns = [c for c in conns if c[0] not in wps or c[1] not in wps]
    check("所有连线节点都存在", len(bad_conns) == 0,
          f"坏连线: {bad_conns}" if bad_conns else "")

    # 所有 home_X 必须在路网里
    required_wps = ["home_bear","home_fox","home_cui","home_lan","home_pirate","home_mystic"]
    missing = [w for w in required_wps if w not in wps]
    check("所有 NPC home 在路网里", len(missing) == 0,
          f"缺少: {missing}" if missing else "")

    # 简单 A* 测试：home_bear → home_fox 有路径
    def astar(start, goal):
        import heapq
        adj = {k: [] for k in wps}
        for c in conns:
            adj[c[0]].append(c[1])
            adj[c[1]].append(c[0])
        def dist(a, b):
            return math.hypot(wps[a][0]-wps[b][0], wps[a][1]-wps[b][1])
        heap = [(0, start, [])]
        visited = set()
        while heap:
            cost, cur, path = heapq.heappop(heap)
            if cur in visited: continue
            visited.add(cur)
            path = path + [cur]
            if cur == goal: return path
            for nb in adj[cur]:
                if nb not in visited:
                    heapq.heappush(heap, (cost + dist(cur, nb), nb, path))
        return []

    path = astar("home_bear", "home_fox")
    check("home_bear → home_fox 可达", len(path) > 0,
          f"路径: {' → '.join(path)}" if path else "无路径")

    path2 = astar("home_cui", "home_lan")
    check("home_cui → home_lan 可达", len(path2) > 0)

except Exception as e:
    check("路网 JSON 读取", False, str(e))

# ──────────────────────────────────────────────────────────
# 3. 状态机时序模拟
# ──────────────────────────────────────────────────────────
print("\n── 3. 状态机时序模拟 ─────────────────────────")

class FakeNPC:
    """最小化状态机模拟，不依赖 Godot"""
    WAITING, TRAVELING, PAUSING, SETTLING, IDLE, WANDERING = range(6)
    WANDER_TIMEOUT = 4.0

    def __init__(self, restless=0.6):
        self.state = self.IDLE
        self.depart_delay = 2.0
        self.settle_timer = 0.0
        self.idle_timer = 2.0
        self.wander_timer = 0.0
        self.restless = restless
        self.waypoints = [1, 2, 3]  # 假设3个路点
        self.at_target = False
        self.pos = 0.0
        self.target = 10.0
        self.history = []
        import random
        self.rng = random.Random(42)

    def advance(self, dt):
        prev = self.state
        if self.state == self.WAITING:
            self.depart_delay -= dt
            if self.depart_delay <= 0:
                if self.waypoints:
                    self.waypoints.pop(0)
                self.state = self.TRAVELING
        elif self.state == self.TRAVELING:
            self.pos += dt * 5  # 模拟移动
            if abs(self.pos - self.target) < 0.5:
                if not self.waypoints:
                    self.state = self.SETTLING
                    self.settle_timer = 1.0
        elif self.state == self.SETTLING:
            self.settle_timer -= dt
            if self.settle_timer <= 0:
                self.state = self.IDLE
                self.idle_timer = 1.5
        elif self.state == self.IDLE:
            self.idle_timer -= dt
            if self.idle_timer <= 0:
                if self.rng.random() < 0.5 + self.restless * 0.5:
                    self.state = self.WANDERING
                    self.wander_timer = 0.0
                else:
                    self.idle_timer = self.rng.uniform(1, 3)
        elif self.state == self.WANDERING:
            self.wander_timer += dt
            if self.wander_timer >= self.WANDER_TIMEOUT:
                self.state = self.IDLE  # 超时退出
                self.idle_timer = 1.0

        if self.state != prev:
            self.history.append((self.state, round(self.wander_timer, 1)))

import random

def run_simulation(start_state, start_delay=0.5, max_t=30, dt=0.05,
                   restless=0.6, waypoints=None, pos=0.0, target=0.3):
    """运行模拟，返回经历的所有状态集合"""
    npc = FakeNPC(restless=restless)
    npc.state = start_state
    npc.depart_delay = start_delay
    if waypoints is not None:
        npc.waypoints = list(waypoints)
    npc.pos = pos
    npc.target = target
    seen = set()
    t = 0
    while t < max_t:
        npc.advance(dt)
        seen.add(npc.state)
        t += dt
    return seen

# 场景1：从 WAITING 出发（模拟 NPC 收到新目标）
states_from_wait = run_simulation(FakeNPC.WAITING, waypoints=[1], pos=0.0, target=0.3)
states_seen = states_from_wait

state_names = {0:"WAITING",1:"TRAVELING",2:"PAUSING",3:"SETTLING",4:"IDLE",5:"WANDERING"}
seen_names = {state_names[s] for s in states_seen}
print(f"{INFO}  30s 内经历状态: {seen_names}")

check("经历 TRAVELING 状态", FakeNPC.TRAVELING in states_seen)
check("经历 SETTLING 状态", FakeNPC.SETTLING in states_seen)
check("经历 IDLE 状态", FakeNPC.IDLE in states_seen)
check("经历 WANDERING 状态", FakeNPC.WANDERING in states_seen)

# 验证超时退出
npc2 = FakeNPC()
npc2.state = FakeNPC.WANDERING
npc2.wander_timer = 0.0
for _ in range(int(FakeNPC.WANDER_TIMEOUT / 0.1) + 5):
    npc2.advance(0.1)
check("WANDERING 超时→IDLE", npc2.state == FakeNPC.IDLE,
      f"实际状态: {state_names[npc2.state]}")

# ──────────────────────────────────────────────────────────
# 4. Locations JSON 完整性
# ──────────────────────────────────────────────────────────
print("\n── 4. 非建筑地点注册 ─────────────────────────")
try:
    locs = json.load(open("data/world/locations.json"))
    for loc_id in ["river", "hilltop", "forest_edge"]:
        v = locs.get(loc_id)
        check(f"{loc_id} 坐标已定义", v is not None and len(v) >= 2,
              str(v) if v else "缺失")
except Exception as e:
    check("locations.json 读取", False, str(e))

# ──────────────────────────────────────────────────────────
# 汇总
# ──────────────────────────────────────────────────────────
print("\n── 汇总 ───────────────────────────────────────")
passed = sum(1 for _, ok in results if ok)
total  = len(results)
print(f"通过 {passed}/{total}  {'🎉 全绿' if passed==total else '⚠️ 有失败项'}\n")
sys.exit(0 if passed == total else 1)
