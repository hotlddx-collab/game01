# 怪物森林 技术决策日志

> 新会话按需读。记录为什么这样选，防止"为啥不用 X"重复讨论。

---

## 2026-05-23

### 选 Godot 4.6.3 + DeepSeek API
- Godot 4 GDScript 类型系统成熟，导出友好
- DeepSeek API 兼容 OpenAI 格式，价格低，中文输出质量高
- 俯视 2D 动森风：YSort 容器 + TileMapLayer

### 选 Python FastAPI + WebSocket 做 agent 后端
- Godot ↔ Python 用 WebSocket 长连接，避免每帧 HTTP 轮询
- FastAPI async 天然适合多 NPC 并发推理
- SQLite 本地持久化，开发期零运维

### 色块占位起步（P0-1）
- 先跑通逻辑流，美术资产后期替换
- 建筑用 Building.tscn 场景化（ColorRect → Sprite2D 只需换资源）

---

## 2026-05-24

### TileMapLayer 替代 TileMap node
- Godot 4.3+ 官方废弃 TileMap，拆分为多个 TileMapLayer
- 每层独立 `z_index`，地面/装饰/路径分层管理更清晰
- 迁移：原 TileMap 的 layer 0/1/2 → 各自独立 TileMapLayer 节点

### SpriteFrames 用 Python 生成 .tres
- 手写 .tres 格式易错（Godot 序列化严格）
- Python 脚本模板化生成，参数化换角色 PNG 即可
- 生成后提交 git，Godot 直接用，不需要 EditorScript

### Building 场景化（@export + Marker2D entry_offset）
- `@export var entry_offset: Vector2` → 编辑器拖动调整入口点
- `_ready` 自动注册到 LocationDB
- 废除：JSON 硬编码坐标方案

---

## 2026-05-25

### ChatManager 距离检测 + 冷却限流
- 距离阈值 `@export` 可调，默认 80px
- 冷却 120s 防止 NPC 对同一对话反复触发
- 用信号 `npc_chat_received` 解耦 AgentClient 与 ChatManager

### SpeechBubble 自适应 UI
- `PanelContainer` auto-size 自适应文本长度
- `Polygon2D` 箭头跟随说话方向
- `show_for_duration(text, duration)` 接口，Animal 调用

### AgentManager.trigger_npc_chat 双写记忆
- speaker 用 `speak_to_npc()` 生成一句话 + 写自身记忆
- listener 用 `hear_from_npc()` 写被动记忆
- 两次 LLM call 串行执行（避免竞争）

## 2026-05-26

### B 好感度存储：SQLite 独立表（非 player_profile KV）
- 新表 `affection(animal_id PK, value, updated_at)`，避免 player_profile 频繁更新混杂语义事实
- 范围 [-50, 100]，5 档对称等级（hate/cold/neutral/like/love）
- 后端权威：方便 LLM prompt 注入；客户端只展示

### B delta 规则用关键词（不再加 LLM 调用）
- greet +1；chat 基础 +1；含正向词("喜欢/谢谢/送你/厉害"等) +2；含负向词("讨厌/滚/烦/笨蛋"等) -3
- 正负互斥时取一边，避免 0 和
- 后期可换 LLM 评分，但首版求简

### B 客户端信号独立（不复用 reply_received）
- 新加 `affection_changed(animal_id, value, level, delta)` 信号
- 不破坏 `reply_received` 现有订阅者（main.gd / chat_manager.gd）
- main.gd 用 group("npc") 遍历找目标 Animal，避免依赖 _current_animal

### B 客户端展示：NameLabel 染色（弃 emote sprite）
- 5 档颜色对称：红 / 淡红 / 白 / 淡绿 / 绿
- 弃用 ninja_adventure emote 图标：素材尺寸小、像素脸辨识度低
- 名字本就在头顶醒目，染色直观且零额外 UI

### B delta 规则：节奏改"有事才跳"（弃日常 +1）
- 旧：每次 chat 基础 +1，玩家瞎聊也涨好感 → 数值变化廉价
- 新：chat 普通对话 0，只有命中正向词 +2 / 负向词 -3 才动
- greet 改成"每个 NPC 每游戏日最多 +1"（用 affection.last_greet_day 列）
- 阈值不变：到 like 仍需约 10 次有效正向发言，到 love 约 25 次

### B 飘字：+N/-N → N 个 ❤️/💔
- 数字反馈过工具化，不符合"和善怪物森林"调性
- 按 |delta| 飘多个 emoji（+2 飘 ❤️❤️，-3 飘 💔💔💔），强弱视觉化
- emoji 字体兜底：暂用 Godot 4.6 默认字体内置 fallback；若渲染豆腐再换 NotoColorEmoji

### E 双向 NPC 对话：3 句固定（可配） + 服务端流式驱动
- 轮次选 3 句（speaker→listener→speaker），自然一来一回再收尾，token & 延迟可控
- 后端用 async generator 每生成一句立刻 ws push 一包，包间 sleep（节奏由服务端控制）
- 客户端协议不变，复用现 `npc_chat_received` 信号，每包让对应 speaker 弹气泡
- ChatManager global_cooldown 加到 14s（≥ 3 × 2.5s + LLM 延迟）防止多对同时刷屏

### D 偷听：距离轮询触发 + 视野机制
- 机制：NPC 对话气泡只在玩家< eavesdrop_distance 时显示（不再"全知上帝视角"）
- 玩家看不到远处 NPC 对话，必须靠近（180px）才能"看到"，这才是真正的"偷听"
- 删掉"玩家头顶灰色气泡"反馈，保留后端记忆写入（NPC 事后知道被听到了）
- 在 `ChatManager._on_npc_chat_received` 里检查玩家距离：
  - <180px：显示 speaker 气泡 + 写后端 eavesdrop 记录
  - ≥180px：不显示气泡，但如果后续玩家发来 eavesdrop 请求（协议里不再发）就忽略
- 后续 NPC 在对话中会自然提起"诶你之前在听啊"

---

## Pending 决策（未敲定）

| 问题 | 候选方案 | 状态 |
|------|----------|------|
| B 好感度存储位置 | agent_server SQLite vs Godot JSON | ✅ 选 SQLite 独立表（2026-05-26） |
| D 偷听触发机制 | Area2D 重叠 vs 距离轮询 | ✅ 选距离轮询（2026-05-26） |
| E 双向对话轮次上限 | 2轮 vs 3轮 vs 动态 | ✅ 选 3 句固定可配（2026-05-26） |
| 向量检索（P2）引擎 | sqlite-vss vs chromadb | ⏳ P2 再议 |
