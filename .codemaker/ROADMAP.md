# 怪物森林 开发路线图

> 新会话必读。记录各阶段目标、完成状态、当前 pending 任务。

---

## 大版本阶段

| 阶段 | 内容 | LLM | 状态 |
|------|------|-----|------|
| P0-1 | Godot 骨架、玩家、NPC 日程、对话占位 | ❌ | ✅ 完成 |
| P0-2 | Python 后端 + DeepSeek 接通 | ✅ | ✅ 完成 |
| P0-3 | 短期记忆 + 昼夜滤镜 | ✅ | ✅ 完成 |
| P1   | 多动物、关系网、地图扩展 | ✅ | ✅ 完成 |
| P2   | 反思机制、长期记忆、涌现剧情 | ✅ | ✅ 完成 |
| P3   | 物理世界扩充（物品/地点/装饰/生物/NPC）| ❌ | 🔄 进行中（阶段1完成/阶段3暂停）|

---

## P3：物理世界扩充

> 目标：在不依赖 LLM 想象力的前提下，靠扎实的"已知事物"让对话和场景丰富起来。
> 顺序：1→2→3→4→5，做完一个验收一个，再继续下一个。

### 阶段 1：物品库扩充（16 → 40）

| 类目 | 现 | 新增 | base_value 区间 |
|---|---|---|---|
| 食物 | 7 | +4（meat/noodle/octopus/gourd）| 2 |
| 资源 | 0 | +5（branch/rock/gem_green/gem_red/bar_copper）| 1-3 |
| 工具 | 0 | +5（shovel/hoe/pickaxe/watering_can/sickle）| 2-3 |
| 物件 | 3 | +3（coin_purse/dice/pan_flute）| 1-3 |
| 宝物 | 0 | +5（treasure_small/gold_cup/silver_cup/gold_key/silver_key）| 3-5 |
| 卷轴 | 0 | +4（scroll_fire/scroll_ice/scroll_thunder/scroll_blank）| 1-3 |
| 药水 | 0 | +3（water_pot/heart_pot/medi_pack）| 1-3 |
| **合计** | **16** | **+24** | — |

改动：`agent_server/items.py` + `scripts/item_db.gd` 同步加 24 条。

### 阶段 2：地点扩充（6 → 12）
新地点：河边码头、田野、洞穴、沙漠边缘、废弃村、露营地。
对应 Tileset：Water、Field、Dungeon、Desert、VillageAbandoned、tileset_camp。
每个地点写进 `LocationDB`，部分 NPC schedule 加新行程。

### 阶段 3：动态装饰（零交互氛围）
基于 `res/ninja_adventure/Backgrounds/Animated/`：
- 风车（MillPropeller A/B）→ 面包店旁
- 水车（Watermill A/B）→ 河边
- 旗帜（Flag）→ 广场
- 瀑布（Waterfall 上中下）→ 森林深处
- 摆动花朵 / 植物 → 各处散布
- 水波纹（WaterRipples）→ 水面

实现：用 AnimatedSprite2D + SpriteFrames（从 SpriteSheet 切帧），或 Sprite2D 周期 region 切换。

### 阶段 4：背景生物（不可交互）
`res/ninja_adventure/Actor/Animal/`：鸡、蝴蝶、青蛙、流浪猫、鱼。
让世界有"活物"。实现：简化版 AI（在固定区域随机游走 + 动画）。

### P3 当前进度
- **P3 阶段 1：物品库扩充** ✅ 2026-05-27 完成
  - `agent_server/items.py`：16 → 45 种物品（食物/资源/工具/物件/宝物/卷轴/药水）
  - `scripts/item_db.gd`：同步加 45 条（含 icon 路径，全部验证存在）
  - 6 个 NPC persona JSON 加 `signature_gift` + 更新 `gift_prefs`（覆盖新物品）
- **P3 阶段 3：动态装饰** ⏸️ 暂停
  - 技术框架已完成（`anim_decoration.gd` SHEET/SWAP 双模式）
  - 尝试用 Bamboo Monster 精灵做树木装饰，视觉效果差，已回退
  - **待解决**：需要在 Godot 编辑器里手动预览 tileset 资源，确认可用图块位置后再做
  - **下次继续**：P3 阶段 2（地点扩充）或让用户在编辑器里完成阶段 3

### P3 Pending（阶段 2-5）
- 阶段 2：地点扩充（需 Tilemap 编辑器操作）
- 阶段 3：动态装饰（需先预览资源确认图块）
- 阶段 4：背景生物
- 阶段 5：新 NPC

### ✅ 已完成
- A：SpriteFrames `.tres` 资源（Python 生成）+ Char 素材接入
- C：`SpeechBubble` 自适应 UI（PanelContainer + Polygon2D 箭头）
- **F：加新 NPC（老咸 + 煊赫）** ✅ 2026-05-26 完成
  - `assets/characters/pirate_lao.png` + `pirate_lao.tres`（Char 9 → 老咸，退休海盗）
  - `assets/characters/mystic_xuan.png` + `mystic_xuan.tres`（Char 17 → 煊赫，神秘发光生物）
  - `data/animals/pirate_lao.json`（7 段 schedule，沧桑爱讲故事）
  - `data/animals/mystic_xuan.json`（7 段 schedule，沉默旁观者）
  - `scenes/main.tscn` 加 PirateLao(700,450) + MysticXuan(850,300)
- **B：好感度系统** ✅ 2026-05-26 完成
  - 后端：`agent_server/affection.py`（AffectionStore + 5 档对称等级 + "有事才跳"规则）
  - SQLite 新表 `affection(animal_id PK, value, updated_at, last_greet_day)`，范围 [-50, 100]
  - 等级：hate(<-10) / cold(<0) / neutral(<20) / like(<50) / love(≥50)
  - **节奏**：greet 每个 NPC 每"游戏日"最多 +1（首次/久别重逢）；chat 普通 0，正向词 +2，负向词 -3
  - prompt 注入 `affection_block`，让 NPC 语气随好感变化
  - `reply` 包附带 `{value, level, delta}` 字段
  - 客户端：`AgentClient.affection_changed` 新信号，`Animal.update_affection()` 改 NameLabel 字色（红/淡红/白/淡绿/绿）+ 飘 N 个 ❤️/💔（按 |delta| 个数）
- **E：双向 NPC 对话** ✅ 2026-05-26 完成
  - `Agent.reply_to_npc(other_name, other_species, other_line, ctx)` 新方法
  - `AgentManager.trigger_npc_chat_session(...)` 异步生成器，按 turns（默认 3）yield 每句
  - speaker→listener→speaker 交替；每句双方记忆都写
  - main.py `_handle_npc_chat` 流式：每句 LLM 完成立刻 ws 发包，包间 sleep `NPC_CHAT_GAP_SEC` (默 2.5s)
  - 客户端协议不变（仍 `npc_chat_received` 信号），靠后端节奏自然驱动每个 NPC 头顶气泡轮流弹
  - `ChatManager` global_cooldown 4→14s，pair_cooldown 25→30s（让一段对话讲完再开新对）
  - 配置项：`NPC_CHAT_TURNS` / `NPC_CHAT_GAP_SEC` 环境变量
- **D：偷听系统** ✅ 2026-05-26 完成
  - 触发：距离轮询（在 `ChatManager._on_npc_chat_received` 里检查玩家到对话中点距离），不另加 Area2D
  - 玩家加 group("player")
  - 半径默认 180px（`ChatManager.eavesdrop_distance`）
  - 视野机制：NPC 对话气泡只在玩家 < 180px 时显示（远处看不到）
  - 新协议 `eavesdrop` C→S：后端 `_handle_eavesdrop` 给 speaker / listener 各写一条 event 记忆 + 写 world_events（actor=player）
  - NPC 后续反思 / 对话时能感知"上次被玩家听到过 X"
- **G：基础表现优化** ✅ 2026-05-26 完成
  - **站位错开**：Animal 到达 location 时，按 `(animal_id+loc)` 哈希加确定性偏移（18-35px 半径），同 NPC 同 location 偏移恒定不抖
  - **说话者弹一下**：show_speech_bubble 触发 sprite scale 1.0→1.18→1.0 一秒回弹，多人重叠时能看清谁在说
  - **▼ 交互目标提示**：玩家持续追踪 hover NPC，目标头顶显示黄色 ▼ 箭头，按 E 前就知道会跟谁交互
- **H：NPC 状态机（互斥/打断/朝向）** ✅ 2026-05-26 完成
  - `Animal.BusyState`：FREE / TALKING_PLAYER / TALKING_NPC，含自动过期 (`_busy_until`)
  - busy 时 `_physics_process` 停止移动，velocity=0
  - `Animal.face_to(pos)` 立即朝向（flip_h + last_dir）
  - 玩家 E：检查 target.is_busy()，busy 则不开对话；▼ 在 busy 时变红
  - 玩家对话开始：set TALKING_PLAYER + face_to(player)；结束：clear_busy() 恢复日程
  - 玩家走远 > `auto_close_distance(130px)` → main.gd 自动关闭对话
  - ChatManager 触发 npc_chat：双方 set TALKING_NPC(lock=global_cooldown 14s)，互相 face_to；超时自动清
  - ChatManager `_is_pair_eligible` 排除 busy NPC，避免 NPC 同时多个对话
- **C：礼物系统** ✅ 2026-05-26 完成
  - 8 种物品（base 3-15）：flower/feather/mushroom/fish/bread/herb/crystal/ancient_book
  - 后端 `agent_server/items.py` + `agent_server/gifts.py`：
    - 公式 `delta = round(base × pref_mult × affection_mult × fatigue_mult)`
    - pref_mult：loves×2.0 / likes×1.3 / neutral×1.0 / dislikes×0.3 / hates×-1.5
    - affection_mult：hate×0.2 / cold×0.5 / neutral×1.0 / like×1.2 / love×1.5
    - fatigue_mult：`1.0 - 0.3 × count` clamp [-0.5, 1.0]，每 2 游戏日 -1 自然淡忘
    - 同 NPC 同物品送多了 mult 会变负 → 反感（"你只送这个吗？"）
  - SQLite 新表 `gift_log(animal_id, item_id, count, last_gift_day)`
  - LLM 不决定数值，只生成反应文本（按 delta 量级提示语气）
  - 6 个 NPC persona JSON 各加 `gift_prefs: {loves/likes/dislikes/hates}`
  - 协议 `gift` C→S，`reply` 包附带 `gift: {item_id, delta, pref, count_after, ...}`
  - 客户端：
    - `PlayerInventory` autoload（dict 存储 + 信号）
    - `ItemDB` autoload（id → name/icon/base_value）
    - `ItemPickup` 节点（地图散落，按 E 拾取）
    - `Player._find_closest_interactable` 同时找 NPC + pickup
    - `DialogUI` 加 🎁送礼按钮 + GiftPicker 网格弹窗
    - `AgentClient.gift_received` 信号
  - 8 个 ItemPickup 散布在镇上各处

### 🔄 进行中
- **P2-1：反思机制** ✅ 2026-05-27 完成
  - 触发：客户端 22:00 发 `time_tick`，后端检查 game_day > last_reflect_day → 触发全员反思
  - LLM 输入：当日 event 记忆（最近 20 条）+ 世界事件（6 条）+ 好感度 + NPC 名单
  - LLM 输出：3-5 条 JSON `{content, importance(1-10), tags, target_id?}`
  - 存储：独立 `reflections` 表 + `animal_intents` 表（intent 类反思自动写入）
  - 注入：每次对话 prompt 加 `【你最近的想法】` 块（最近 7 天，importance>=4，最多 5 条）
  - debug：`POST /debug/reflect/{animal_id}/{day}` 强制触发（force=True）

- **P2-C：importance 自动评分** ✅ 2026-05-27 完成
  - `fact_extractor.py` 扩展关键词表（情感词/互动词/承诺词，共 ~30 项）
  - 新增 `importance_for_gift(delta)` 4 档映射
  - agent.py 所有 memory.add 改用 `estimate_importance(text, base=N)`
  - 分布：普通闲聊 3-4 / 礼物 6-8 / 名字/承诺/吵架 7-9

- **P2-B：反思驱动 NPC 自发行为** ✅ 2026-05-27 完成
  - 反思 tags=["intent"] 自动写 `animal_intents(animal_id, target_id, intent_text, game_day, activate_hour)`
  - 意图激活：每小时 `time_tick` 时检查，时间到 → 推送 `npc_intent` WS 消息
  - 客户端：`AgentClient.npc_intent_received` 信号 → `main.gd._on_npc_intent` → 两 NPC free 则触发 `request_npc_chat`

### ⏳ Pending
（P1 全部完成，等开始 P2 反思 / 长期记忆 / 涌现剧情）

---

## WebSocket 消息类型表

| type | 方向 | 说明 |
|------|------|------|
| `dialog` | C→S | 玩家发起与 NPC 对话 |
| `dialog_reply` | S→C | NPC 回复玩家（含 `affection: {value, level, delta}`） |
| `npc_chat` | C→S | 触发两 NPC 互动 |
| `npc_chat_reply` | S→C | NPC 互动结果（含 speaker_id, listener_id, text，多轮时一句一包） |
| `eavesdrop` | C→S | 玩家偷听到 NPC 对话，后端写双方 event 记忆 + 世界事件 |
| `gift` | C→S | 玩家送礼给 NPC（item_id），后端走公式算 delta + 写记忆 + LLM 反应文本 |
| `time_tick` | C→S | 游戏时间心跳（每游戏小时一次），后端 22:00 触发全员每日反思，无回包 |

---

## 现有 NPC 列表

| id | 名字 | 种族 | 职业 | 状态 |
|----|------|------|------|------|
| `bear_baker` | 苔老板 | 苔藓巨人 | 面包师 | ✅ 在场 |
| `fox_postman` | 焰仔 | 火苗精灵 | 邮差 | ✅ 在场 |
| `herbalist_cui` | 崔草草 | 草药师 | 药剂师 | ✅ 在场 |
| `traveler_lan` | 蓝旅 | 旅行者 | 旅行者 | ✅ 在场 |
| `pirate_lao` | 老咸 | 退休海盗 | 老水手 | 🔄 建设中 |
| `mystic_xuan` | 煊赫 | 神秘生物 | 旁观者 | 🔄 建设中 |

---

## Autoload 单例列表

| 单例名 | 脚本 | 职责 |
|--------|------|------|
| `WorldClock` | `scripts/world_clock.gd` | 游戏时间 tick |
| `LocationDB` | `scripts/location_db.gd` | 地点注册 + 查询 |
| `ChatManager` | `scripts/chat_manager.gd` | 距离检测 + 冷却 + 触发 NPC 互动 |
| `AgentClient` | `scripts/agent_client.gd` | WebSocket 与 Python 后端通信 |
| `PlayerInventory` | `scripts/player_inventory.gd` | 玩家库存（item_id → count） |
| `ItemDB` | `scripts/item_db.gd` | 物品定义（id/name/icon/base_value） |
