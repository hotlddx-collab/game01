# 镇长养成核心循环设计

> 状态：设计稿 v1（2026-06-04）
> 范围：本文是后续所有"镇长系统"开发任务的总宪法，每个任务从这里取设计点拆实现。
> 修订原则：**机械骨架可循环，LLM 只填血肉**。所有数值/规则用 SQLite 持久化，所有文本用 LLM 生成。

---

## 1. 设计目标

| 目标 | 衡量 |
|---|---|
| 玩家有持续的"我为什么玩"目标 | 当镇长 → 维持任期 → 落选反扑 → 再当选 |
| 跟 NPC 聊天 / 刷好感度有明确下游 | affection 直接折算选票 |
| 不依赖一次性主线剧情，循环耐玩 | 每任期对手行为/危机事件随机化，LLM 实时生文本 |
| 复用现有零件（quests / gifts / milestones / reflection） | 重定位而非新做 |
| 让玩家**取舍**而非全收 | 时间有限 + 权力点有限 + 承诺要兑现 |

**反目标**（明确不做）：
- 不写线性主线剧情
- 不靠美术演出撑（仅复用现有 milestones 风格的横幅 + 文本）
- 不引入战斗 / 数值堆叠

---

## 2. 节奏：7 游戏日 = 1 任期周期

```
D1 ──── D2 ──── D3 ──── D4 ──── D5 ─── D6 ──── D7
竞选/任期日常                       辩论日   投票/结算日
```

> "任期"和"竞选"在节奏上同形：**当选者过的就是任期日常 + 倒数日总结演讲；落选者过的就是在野党搞事 + 倒数日下一轮竞选**。同一时间轴双轨，UI 切换文案。

### 每日节拍（统一）

| 时段 | 玩家事 | 系统事 |
|---|---|---|
| 06:00 醒来 | 看声望榜 + 今日 NPC 诉求 | 对手 NPC 生今日动作（LLM） |
| 09:00–17:00 | 拜访 / 拉票 / 兑现承诺 / 处理危机 | 危机事件按概率触发 |
| 18:00 | 收工，可二选一：写宣言 / 偷听对手 | 民意结算 |
| 22:00 | （NPC 反思 → 投票倾向重算） | 全员 reflection 注入"今日发生了 X，我对候选人 Y 看法变..." |

### 关键日

- **D6 辩论日**：3 个 NPC 提问，玩家选答（4 选项）+ 对手 LLM 反驳 → 民众（其余 NPC）即时评分
- **D7 投票日**：上午公布榜单冻结 → 中午每个 NPC 按 weight 扔票 → 下午演说 → 晚上结算 → 明日 D1 进入新任期

---

## 3. 投票公式

每 NPC 对每候选人有 `weight ∈ [0, 100]`，每日 22:00 由后端重算：

```
weight = clamp(
    affection_norm(50) +              # affection [-50,100] → [0, 50]
  + promise_score(20) +               # 承诺兑现率 × 20
  + debate_score(10) +                # 辩论日表现（其他日为 0）
  + recent_event_score(15) +          # 最近 3 天有利/不利事件加权 [-15, +15]
  + base_loyalty(5)                   # 候选人是该 NPC"亲近圈"则 +5
  + opponent_smear(-X) + ally_push(+Y) # 对手抹黑 / 玩家盟友拉票
,  0, 100)
```

> 投票日每 NPC 把 100% 票投权重最高的候选人；并列则随机。

**胜负**：得到多数 NPC（≥ 4/6）→ 当选 / 连任。否则败。

> 可调参数全集中在 `agent_server/election.py` 顶部常量。

---

## 4. 对手 NPC

### 选谁

**对手随赛季轮换**（用户决策 v1.1）。每个新任期开始时由后端 `select_opponent()` 选出本期对手：

| 候选 | 适合做对手的角度 | 优先级 |
|---|---|---|
| bear_baker 苔老板 | 社区元老、保守派 | 高（首期默认） |
| fox_postman 焰仔 | 人脉广、信息掌控者 | 高 |
| herbalist_cui 小翠 | 民生牌、关怀派 | 中 |
| pirate_lao 老咸 | 反建制、平民派 | 中 |
| traveler_lan 蓝旅 | 外来视角、改革派 | 中 |
| mystic_xuan 煊赫 | 慎用，沉默人设破坏成本高 | 极低 |

**轮换规则**：
- 第 1 任期：`bear_baker`（保底首期戏剧性）
- 第 N+1 任期：从未当过对手的池子里随机抽，用尽则按"距上次担任最久"加权抽
- 候选池排除当前对玩家 affection ≥ love 的 NPC（不让"挚友"被强行设为对手）
- 同一 NPC 两次担任对手之间至少间隔 2 任期

实现位置：`agent_server/election.py::select_opponent(term_id)`，调用时机 = 上一任期 D7 投票结算后立即决定下一期对手。

**对手立场动态化**：每次担任对手时由 LLM 基于 NPC 的 important_facts + 当前世界事件生成"竞选纲领"（3-5 条立场），写入 `candidate_state.platform_json` 持续整任期使用。

### 对手 AI 规则（每日生 1-2 个动作）

每天 07:00 后端为对手生今日 action plan，每个 action 同时落地：
1. **机械效果**：调整某 NPC 对玩家/对手的 weight 子项
2. **LLM 文本**：对手在该 NPC 面前说什么（写入双方记忆 + 可能被玩家偷听）

| Action | 机械效果 | LLM 生 |
|---|---|---|
| 拜访拉拢 | 目标 NPC 对苔老板 affection +1，base_loyalty 倾斜 | 拜访台词 |
| 散播怀疑 | 目标 NPC 对玩家 recent_event_score -3 | 怀疑台词 |
| 提反承诺 | 添加一条"承诺池"对照项 | 承诺文本 |
| 公开演讲 | 全员 -2 玩家 weight | 演讲文本 |

每日动作权重由对手当前声望决定（落后时激进，领先时保守）。

### 玩家反制手段

| 手段 | 效果 |
|---|---|
| 偷听对手谈话 | 解锁"反驳卡"，下次拜访该 NPC 可消除 -3 抹黑 |
| 拜访同一 NPC 给好礼 | 抵消 base_loyalty 倾斜 |
| 公开揭穿 | 高风险高回报：成功 -5 对手 weight，失败 -5 玩家 weight |

---

## 5. 承诺系统

**重定位现有 12 quests**：每条 quest 现在不再随机派发，而是：

- 玩家**主动**在某 NPC 头顶看到"诉求气泡"（NPC 想要的事）→ 接 = "承诺"入承诺池
- 承诺有 deadline（默认任期结束前）
- 兑现 → `promise_score` 加分；该 NPC affection +5
- 未兑现 → 任期结束时 `promise_score` 减分；该 NPC -10 affection 且 reflection 注入"他/她食言了"

承诺池上限 5 条（迫使玩家挑），超出需放弃旧承诺（公开承诺打破 = 重罚）。

---

## 6. 辩论日（D6）

UI = 全屏对话风格 panel。流程：

1. 系统抽 3 个 NPC（不含玩家亲近圈，避免送分）
2. 每人提一个问题（基于其 important_facts/诉求 LLM 生成）
3. 玩家从 4 选项选一答（4 个选项由后端按"激进/保守/讨好/务实"四象限提供）
4. 对手 LLM 反驳玩家答案
5. 全场其余 NPC 即时评分（每人对玩家 debate_score ±[0, 10/3]）

最终 debate_score 投入 weight 公式，仅 D6 → D7 期间生效。

---

## 7. 任期权力（当选后开放）

每日 06:00 给 3 点"权力点"，可花在：

| 行动 | 花费 | 效果 |
|---|---|---|
| 指派 NPC 任务 | 1 | 该 NPC 当日 schedule 替换为指派任务（完成则 +affection；做不到则 -affection） |
| 召开议会 | 2 | 全员到广场（强制中止 schedule），可发表施政发言（影响所有 weight 子项） |
| 发布公告 | 1 | 影响一类危机事件的处理速度 |
| 私下协调 | 1 | 让两个 NPC 关系 +/- |

权力点不可累积。**有限性 = 玩家必须取舍**。

---

## 8. 危机事件池

任期内每日按 30% 概率触发一个事件（D6/D7 不触发）。事件模板在 `data/world/crises.json`，至少 10 条：

| 模板 | 触发条件 | 解决路径 |
|---|---|---|
| 面包店火灾 | 苔老板在场 | 派 2 NPC 救火 / 个人去 |
| 草药短缺 | 任期 D2+ | 答应小翠预算 / 自己采集 |
| 老咸醉酒闹事 | 老咸 affection<20 | 调解 / 强行送回 |
| 财政赤字 | 累计权力点 >5 | 加税（-NPC affection）/ 缩公共服务 |
| 神秘失踪 | 煊赫 affection 低 | 寻人任务 |
| ……（共 10-15 条） | | |

每事件机械结构 + LLM 生具体台词。**处理慢 / 不公 → 民意下滑（recent_event_score -）**。

---

## 9. 落选 = "在野党"模式

落选不重开。进入在野模式：
- 失去权力点
- 解锁"调查现镇长"行为：每天可花时间偷听镇长 NPC 谈话、收集失误证据
- 收集到的"丑闻卡"在下届竞选 D6 辩论可打出，重创对手
- 在野期 base_loyalty 反向（"政府对立面"加成）
- 任期结束 → 新一轮竞选（**对手轮换**，不一定是上届当选者）

**affection 不重置**（用户决策 v1.1）：跨任期沿用，让玩家长期经营有累积价值。

**不允许任期内主动辞职**（用户决策 v1.1）：必须扛完整 7 天任期。

---

## 10. 复用零件映射

| 零件 | 旧用途 | 新用途 |
|---|---|---|
| `affection.py` | 私聊好感度 | 投票 weight 主项 |
| `gifts.py` | 给好感度送礼 | 拉票手段（套娃式机制不变） |
| `quests.py` | 随机派发任务 | 承诺池入口 + 兑现判定 |
| `milestones.py` | 好感度阶段演出 | 改造为"任期关键事件"演出框架 |
| `reflection.py` | 每日反思 | 反思中插入"今日选举想法" |
| `world_events.py` | 世界共享 lore | 对手抹黑/玩家拉票事件源 |
| `chat_manager.py` | NPC 间互动 | 用于辩论日 NPC-NPC 互动 |

---

## 11. 数据模型（新表）

```sql
-- 任期/选举
CREATE TABLE election_term (
  term_id INTEGER PRIMARY KEY AUTOINCREMENT,
  start_day INTEGER,
  end_day INTEGER,
  winner_id TEXT,           -- 'player' | npc_id
  result_json TEXT          -- 完整结算快照
);

-- NPC 投票权重（每日重算后历史保留）
CREATE TABLE election_weight (
  term_id INTEGER,
  game_day INTEGER,
  voter_id TEXT,            -- npc_id
  candidate_id TEXT,        -- 'player' | 'bear_baker'
  weight REAL,
  breakdown_json TEXT,      -- 公式各项明细，调试用
  PRIMARY KEY (term_id, game_day, voter_id, candidate_id)
);

-- 候选人 / 当前任期
CREATE TABLE candidate_state (
  candidate_id TEXT PRIMARY KEY,  -- 'player' | npc_id
  term_id INTEGER,
  is_incumbent INTEGER,
  power_points INTEGER DEFAULT 0,
  power_points_max INTEGER DEFAULT 3
);

-- 承诺池
CREATE TABLE promises (
  promise_id INTEGER PRIMARY KEY AUTOINCREMENT,
  term_id INTEGER,
  candidate_id TEXT,
  npc_id TEXT,              -- 受益方
  quest_id TEXT,            -- 复用 quests
  status TEXT,              -- pending / fulfilled / broken
  accept_day INTEGER,
  deadline_day INTEGER
);

-- 危机事件实例
CREATE TABLE crises (
  crisis_id INTEGER PRIMARY KEY AUTOINCREMENT,
  term_id INTEGER,
  template_id TEXT,
  trigger_day INTEGER,
  status TEXT,              -- active / resolved / ignored
  resolution_path TEXT,
  reputation_delta REAL
);

-- 对手当日动作日志
CREATE TABLE opponent_actions (
  action_id INTEGER PRIMARY KEY AUTOINCREMENT,
  term_id INTEGER,
  game_day INTEGER,
  candidate_id TEXT,
  action_type TEXT,         -- visit / smear / promise / speech
  target_npc TEXT,
  llm_text TEXT,
  mechanical_effect_json TEXT
);
```

---

## 12. UI 总览

| 界面 | 触发 | 内容 |
|---|---|---|
| 竞选 HUD（左上常驻） | 选举进行中 | 候选人对照声望条 + D? / 7 倒计时 + "今日该做的事" |
| 承诺面板（按 P 键） | 任意 | 承诺池列表，颜色标识：未兑/已兑/破碎 |
| 危机横幅 | 触发时 | 红色横幅 + 解决路径选项 |
| 对手日志（按 O 键） | 任意 | 对手最近 3 天动作明细（玩家偷听到的） |
| 辩论 UI | D6 | 全屏对话框 + 4 选项 |
| 投票演出 | D7 | 票数实时累计动画 + 结算横幅 |
| 权力点 HUD | 任期内 | 3 个圆点 + 可点击行动菜单 |
| 在野党面板 | 落选后 | 调查目标、丑闻卡列表 |

---

## 13. MVP 切片（最小可玩任期）

实现顺序对应 todo：

1. ✅ 本文档
2. 选举后端：表 + weight 重算 + 周期推进（不含对手 AI）
3. 对手 AI：先做最简版（每日 1 个 visit 动作）+ 赛季换人
4. 竞选 HUD：声望条 + 倒计时
5. 承诺系统：现有 quests 接入
6. 辩论日 + 投票日演出
7. 任期权力点 v1
8. 危机事件池（10 条）
9. **赛季搬家系统**（最简：1 走 1 来，新人空白关系）
10. 落选在野党模式
11. 连任难度阶梯 + 称号 v1
12. 平衡圈调

**MVP 完成判定**：玩家能完整跑完 2 个连续 7 天任期（含一次搬家），无论赢输都能继续，循环不断裂。

---

## 14. 未决问题（Q&A 待用户拍板）

每条都有合理默认，开发可先按默认走，验收时调。

| Q | 默认 | 备选 |
|---|---|---|
| 选举周期长度？ | **7 游戏日** ✅ 已锁 | — |
| 首期对手是谁？ | **bear_baker** 首期保底，第 2 期起轮换 ✅ 已锁 | 见 §4 选谁 |
| 跨任期 affection 是否重置？ | **不重置** ✅ 已锁（NPC 不会失忆） | — |
| 落选连续多少届触发结局？ | **不做特殊结局**，改为连任难度+成就 ✅ 已锁 | 见 §16 |
| 任期内可主动辞职？ | **否** ✅ 已锁 | — |
| 每赛季 NPC 流动？ | **0-2 搬走 + 0-2 搬入**（带新空白关系） ✅ 已锁 | 见 §15 |

---

## 15. 赛季搬家系统（v1.1 加入）

> 设计动机：affection 跨任期不重置（合现实，居民不会失忆），但赛季体验需要差异化。**靠 NPC 流动而非记忆清零**来制造每个赛季的新感觉。

### 核心规则

- **常驻人口**：6-8 人（当前 6 人为下限）
- **NPC 总池**：≥ 12 人（待制作中等优先级新 NPC 6 人备选）
- **每赛季流动**（D7 投票结算后立即结算）：
  - 70% 概率搬走 1 人
  - 20% 概率搬走 2 人
  - 10% 概率本季无搬走
- **搬入数 = 搬走数 + (0 或 1 偶发)**（保持常驻 6-8 浮动，可缓慢扩张）

### 搬走候选优先级

按"剧情合理性"加权抽签（不纯随机，避免人气王突然走人破坏体验）：

| 因素 | 权重 |
|---|---|
| 玩家 affection ≤ -10 | × 3.0 |
| 玩家 affection ≤ 0 | × 1.8 |
| 与场内其他 NPC 平均 affection 低 | × 1.5 |
| 上届对手 + 落选 | × 1.4（"心灰意冷离镇") |
| affection ≥ 50（挚友） | × 0.1（基本不会走） |
| 当前镇长（玩家或 NPC） | × 0（任期未结束不可搬） |
| 在场不足 4 个赛季 | × 0.5（新人不会立刻又走） |

抽到的 NPC 会在 D7 演出后宣布搬走，下一任期 D1 起从场上消失（schedule 停用、不再参与投票/对话）。**记忆和 affection 数据保留**，未来回归时延续。

### 搬入候选池

`data/world/npc_pool.json` 维护未在场的 NPC 列表，每条含：

```json
{
  "id": "char_X_yyy",
  "name": "...",
  "species": "...",
  "important_facts": [...],
  "schedule": [...],
  "arrival_hooks": [
    "听说这镇上前任镇长搞了 XX 改革",
    "因为 YY 原因离开旧镇"
  ],
  "min_arrival_term": 2,         // 至少第 2 任期才可能搬入
  "tags": ["改革派", "商人"]     // 影响后续可作为对手的池
}
```

### 搬入时关系初始化

- 对所有在场 NPC：`affection = 0`，`base_loyalty = 0`
- 对玩家：`affection = 0`
- 一条**入镇剧情**：到场后第一天 LLM 触发广场亮相演讲（写入 world_events，所有 NPC reflection 注入"镇上来了新人 X，TA 的态度是 ..."）
- 新居民对老居民"耳闻不识"：prompt 中加入"你刚到镇子，对其他居民只听过名字，没接触过"
- 第 1 个在场赛季内**不能担任对手**（保护新人体验，防玩家瞬间被陌生人拉票战）

### 与对手轮换的耦合

- 搬入新 NPC 进入下下次对手候选池
- 上届对手若搬走 → 不影响本届对手选择（已基于轮换池抽过）
- 对手若搬走（在野期间），保留其历史数据但其在 candidate_pool 标记 inactive

### 玩家体验差异化指标

每赛季都至少有一项"和上赛季不同"：
- 至少有一个新对手（轮换保证）
- 大概率有人事变动（85% 概率）
- 全员对玩家"近期事件评分"重置（仅本子项重置，affection 主体不动）

### UI

- D7 结算演出后：📜 搬家公告横幅（"X 离开了镇子" / "新居民 Y 抵达"）
- 搬入次日：广场自动聚集所有在场 NPC，玩家走近触发观看欢迎仪式
- NPC 名册（按 N 键）：在场 / 历史搬走 / 等待回归 三 Tab

### 数据模型补充

```sql
CREATE TABLE residents (
  npc_id TEXT PRIMARY KEY,
  status TEXT,              -- 'active' | 'departed' | 'pending_arrival'
  arrived_term INTEGER,     -- 首次到场任期
  departed_term INTEGER,    -- 最近一次离开任期（NULL 若未走）
  total_active_terms INTEGER DEFAULT 0
);

CREATE TABLE migration_log (
  log_id INTEGER PRIMARY KEY AUTOINCREMENT,
  term_id INTEGER,
  npc_id TEXT,
  direction TEXT,           -- 'depart' | 'arrive'
  reason_text TEXT          -- LLM 生成
);
```

### MVP 范围

阶段一只做：
- 池子配 3 个新候选 NPC（不写完整 schedule，先用模板生）
- 70% 搬走概率 + 50% 搬入概率
- 离开/到来公告横幅 + 名册 UI
- 新居民关系归零 + 不可任对手限制

后续：欢迎仪式演出、回归机制、池扩到 12+。

---

## 16. 连任难度与成就（v1.1 加入）

> 设计动机：用户拒绝"3 届落选"特殊结局；改为**连任越久压力越大，但奖励也越大**，把连任本身做成成就追求。

### 难度梯度

任期序号 N（玩家连任为镇长的次数，落选则归零）：

| 任期 | 对手智能 buff | 危机频次 | 危机难度 | 承诺池上限 |
|---|---|---|---|---|
| 1 | 基础 | 30%/日 | 简单 | 5 |
| 2 | +1 动作/日 | 35%/日 | 简单 | 5 |
| 3 | +1 动作/日 + 反击玩家偷听 | 40%/日 | 中等（多目标） | 4 |
| 4 | 联合其他 NPC 围剿 | 45%/日 | 中等 | 4 |
| 5 | 出现"民意疲劳"（base_loyalty 反向衰减） | 50%/日 | 难 | 3 |
| 6+ | "传奇模式"：双对手 / 政变事件触发 | 55%/日 | 难 | 3 |

落选则 N 归零。**新一任以挑战者身份起，对手是当前镇长**。

### 称号系统

每达成里程碑解锁一个称号（HUD 显示在玩家名下）：

| 称号 | 条件 | 加成 |
|---|---|---|
| 新晋镇长 | 首次当选 | — |
| 二连霸 | 连任 2 届 | 权力点 +1（任期内单次） |
| 三朝元老 | 连任 3 届 | 解锁议会"长老议程" |
| 不倒翁 | 连任 5 届 | 全员 base_loyalty +2 |
| 镇长传说 | 连任 7 届 | 解锁特殊危机/事件分支 |
| 草根领袖 | 在野后再当选 | 在野期间投票得票上限解锁 |
| 民心所向 | 单届全 NPC 投票（6/6） | 永久承诺池 +1 |

称号永久保留（即使落选也保留头衔，仅 N 归零）。

### 数据模型补充

```sql
CREATE TABLE player_career (
  id INTEGER PRIMARY KEY DEFAULT 1,
  consecutive_terms INTEGER DEFAULT 0,
  total_won_terms INTEGER DEFAULT 0,
  total_lost_terms INTEGER DEFAULT 0,
  unlocked_titles_json TEXT
);
```

---

> 文档结束。后续任务文件应在每个开头引用本文档对应章节号。
