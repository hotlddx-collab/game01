# 怪物森林 项目开发规范

> **每次开发前 AI 必读**。新会话第一件事就是读这份文件。

---

## 项目概述

Agent 驱动的 2D 怪物森林游戏（动森风格）。差异化：怪物 NPC 由 LLM 驱动，比传统 NPC 聪明得多——能记住玩家、按性格回应、自主生活。世界观：怪物森林——住满奇形怪状但和善的怪物居民，玩家是误入森林的旅人。

- **引擎**：Godot 4.6.3
- **视角**：俯视 2D（稍 45° 斜视），动森风
- **后端**：Python FastAPI + WebSocket
- **LLM**：DeepSeek API（OpenAI 兼容格式）
- **记忆**：SQLite（持久化）+ 关键词检索（P0-3）/ 向量检索（P2+）
- **美术**：BDragon1727 / 72 Cute Pixel Character（chibi 风像素，64×64 单帧）

## 当前阶段

**P0 阶段 1 进行中**：Godot 端骨架，硬编码日程，无 LLM。

## 路线

| 阶段 | 内容 | LLM | 验收点 |
|---|---|---|---|
| P0-1 | Godot 骨架、玩家、NPC 日程、对话占位 | ❌ | F5 跑起，NPC 按时换地点，按 E 弹占位对话 |
| P0-2 | Python 后端 + DeepSeek 接通 | ✅ | 对话用 LLM 实时生成，符合性格 |
| P0-3 | 短期记忆 + 昼夜滤镜 | ✅ | 动物记得玩家说过的话 |
| P1 | 多动物、关系网、地图扩展 | ✅ | — |
| P2 | 反思机制、长期记忆、涌现剧情 | ✅ | — |

---

## 一、Godot 标准制作规范（强制 ⚠️）

> **核心原则**：能在编辑器里配置的，绝不在代码里运行时构造。能用 Godot 内置节点解决的，绝不自己造轮子。这套规范是底线，**不允许走野路子**——否则项目后期问题指数级增长。
>
> AI 助手如发现现有代码违规，应先指出，给出迁移方案，等用户确认后再改。

### 1.1 资源（Resource）
**所有可视化、可复用的数据必须是 `.tres` / `.res` 资源文件，编辑器可见可调**：

| 资源类型 | 用途 | 文件位置 |
|---|---|---|
| `SpriteFrames` | 角色动画 | `assets/characters/{id}.tres` |
| `Theme` | UI 主题 | `assets/themes/*.tres` |
| `TileSet` | 地图瓦片集 | `assets/tilesets/*.tres` |
| `LabelSettings` | Label 字体样式 | `assets/ui/*.tres` |
| `StyleBox*` | UI 边框背景 | 嵌入 Theme 或单独 `.tres` |
| `Curve` / `Gradient` | 曲线/渐变 | `assets/curves/*.tres` |
| `AudioStream` | 音频 | `assets/audio/*.{ogg,wav}` |

**禁止**：用 `.new()` 在代码里构造 SpriteFrames / Theme / TileSet 等运行时塞进节点。  
**允许**：开发期用 Python/工具脚本一次性生成 `.tres`，提交进 git。

### 1.2 场景与节点
- **每个可复用实体一个 `.tscn`**：Player、Animal、Building、UI 组件等
- **禁止纯代码 `add_child(Node.new())`** 拼装实体（除非是临时粒子、子弹这种短生命周期）
- 节点命名：**PascalCase**（`AnimalNPC`、`DialogPanel`）
- 脚本/场景文件名：**snake_case**（`animal.gd`、`dialog.tscn`）
- 复杂对象用**容器节点**分组（如 `Node2D "Buildings"` 下挂 5 个建筑场景）
- 实例化用 `instance=ExtResource(...)`，**不要**代码里 `preload(...).instantiate()` 除非动态生成

### 1.3 节点选型（不要造轮子）
| 用途 | 标准节点 | 不要用 |
|---|---|---|
| 玩家/NPC 角色 | `CharacterBody2D` | ❌ `RigidBody2D`、`Node2D` |
| 寻路 | `NavigationAgent2D` + `NavigationRegion2D` | ❌ 自写 A* |
| 触发器 | `Area2D` + `CollisionShape2D` | ❌ 距离判断 polling |
| 地图 | `TileMap` + `TileSet` | ❌ ColorRect 拼建筑、Sprite2D 拼地砖 |
| UI 根 | `Control` 在独立 `CanvasLayer` | ❌ Node2D 加 Label |
| Z 排序 | `YSortEnabled` 容器 + `y_sort_origin` | ❌ 手动 `z_index` 调每帧 |
| 全局服务 | autoload 单例 | ❌ 全局静态变量乱传 |
| 摄像机 | `Camera2D` | ❌ 手动平移视图 |
| 动画 | `AnimatedSprite2D` + `SpriteFrames` 或 `AnimationPlayer` | ❌ `_process` 里手切 region |
| 音频 | `AudioStreamPlayer*` | ❌ 自己 mix |
| 输入 | `InputEvent` + `Input` | ❌ 自轮询 OS |
| 多语言 | `tr("KEY")` + `.po` 文件 | ❌ 字典硬编码 |
| 存档 | `ResourceSaver.save()` 或 JSON | ❌ 自定义二进制 |

### 1.4 美术资源标准（像素风专项）

**导入设置**（Inspector 的 Import 面板）：
- **Filter**: `Nearest`（保留像素硬边）
- **Mipmaps**: `false`
- **Repeat**: 视情况
- **Preset**: 选 "2D Pixel"

**像素对齐**：
- `project.godot`: `rendering/2d/snap/snap_2d_transforms_to_pixel = true`
- 单位 = 1 像素，**禁止亚像素位置**（角色坐标用 round/snap）

**Sprite Sheet 切帧**：
- 用 `AtlasTexture` sub_resource 在 `.tres` 里切帧
- **禁止**用 `Rect2(...)` 在代码里每帧构造 region

### 1.5 `@export` 标注（强制）
所有"调试时可能要改的数值"必须 `@export`，给默认值 + 注释：

```gdscript
@export var move_speed: float = 100.0           # 像素/秒
@export var interact_radius: float = 32.0
@export_file("*.json") var persona_file: String
@export_file("*.tres") var sprite_frames_file: String
@export_range(0.1, 10.0, 0.1) var time_scale: float = 1.0
@export var enemy_color: Color = Color.RED
@export_node_path("CharacterBody2D") var target_path: NodePath
```

**禁止魔数**：脚本里出现裸 `100.0`、`32.0` 等数字就是错。

### 1.6 通信用信号
组件间用 `signal`，避免硬引用对方节点：

```gdscript
signal dialog_finished(animal_id: String)
signal health_changed(new_hp: int, old_hp: int)
```

父子节点引用用 `%UniqueName`（unique_name_in_owner=true）或 `@onready var x = $Path`。  
**禁止** `get_node("../../sibling")` 这种脆弱引用。

### 1.7 资源路径
- 一律 `res://` 绝对路径，**禁止相对**
- 目录约定：
  - `scenes/` — `.tscn`
  - `scripts/` — `.gd`
  - `assets/` — 美术、音频、`.tres` 资源
  - `data/` — JSON 配置
  - `tools/` — `@tool` 编辑器脚本
  - `addons/` — 第三方插件
  - `agent_server/` — Python 后端

### 1.8 GDScript 风格（强制）
- **类型标注必写**：`var hp: int = 100`，`func get_pos() -> Vector2:`
- **常量大写**：`const TICK_INTERVAL: float = 0.5`
- **`@onready` 拿节点**：`@onready var sprite: AnimatedSprite2D = %Sprite`
- **顶部按顺序**：`@tool` → `class_name` → `extends` → `signal` → `enum` → `const` → `@export` → `var` → `@onready` → `_ready` → `_process` → 其他方法
- **私有方法 `_` 前缀**：`func _update_state() -> void:`
- **避免** `Variant` 类型：能定就定具体类型

### 1.9 数据驱动
- 配置走 JSON / Resource，**不写死在脚本**
- 改一个动物的性格只改 JSON，不动代码
- `data/` 下 JSON 必须可热加载（重启游戏即生效）

### 1.10 工具脚本（`@tool` EditorScript）
一次性资源生成、批处理放 `tools/` 目录：

```gdscript
@tool
extends EditorScript

func _run() -> void:
    # 编辑器里 文件 → 运行（Ctrl+Shift+X）
    pass
```

**优先级**：
1. AI 助手能用 Python 直接产 `.tres` → 用 Python（用户不用开 Godot）
2. **节点级行为**（如自动铺地、自动加载子节点）→ 用 `@tool class_name` + `_ready` 让节点自己干，**不要让用户跑 EditorScript**
3. 真的只能 GDScript 干的批处理 → 才用 `@tool EditorScript`，且要用户最少操作

> ❌ 错误示范：让用户在 Godot 里点 "文件 → 运行" 跑 EditorScript 才能看到效果  
> ✅ 正确做法：F5 跑游戏即看到效果（`@tool` 节点 `_ready` 自动初始化）

### 1.11 性能（避免低级错误）
- 物理用 `_physics_process`，不在 `_process` 里跑物理
- 信号比 `_process` 轮询好
- 大量同类节点用对象池（如子弹）
- `is_instance_valid()` 防野指针
- `Texture2D` 不在 `_process` 里 `load()`，应预加载或 `@onready preload()`

### 1.12 项目设置
- `project.godot` 由编辑器编辑（除非确实只能手改）
- 输入映射在编辑器 Project Settings → Input Map 配置，**不**在代码里 `add_action`
- Layer 命名在 Project Settings → Layer Names 配置

### 1.13 不要走的"野路子"清单 ❌

| 野路子 | 标准做法 |
|---|---|
| ColorRect 当建筑 | 建筑做成 `building.tscn` 场景，含 Sprite2D + StaticBody2D + Label |
| 运行时 `SpriteFrames.new()` 加帧 | `.tres` 资源在编辑器/工具脚本生成 |
| 全局静态变量传值 | 用 autoload 或信号 |
| `_process` 里 `find_node()` 找节点 | `@onready` 提前缓存 |
| 字符串拼路径 | `res://` 绝对 + 常量 |
| `Variant` 满天飞 | 类型化 |
| 硬编码坐标 | 用 `Marker2D` 占位 + 编辑器拖动 |
| Label 当世界标签 | 用独立 UI CanvasLayer + 跟随 follow node |
| 手动 z_index 排序 | `YSortEnabled` 容器 |
| `_input` 里写复杂逻辑 | `_unhandled_input` + 状态机 |
| 自己搞文件 IO 存档 | `ResourceSaver` / JSON + `FileAccess` |

### 1.14 添加新角色的标准流程

```
1. PNG 放 assets/characters/
2. AI 助手用 Python 脚本生成 .tres（参考之前的生成代码）
   或：tools/build_sprite_frames.gd 在 Godot 里 File → Run
3. 在 persona JSON 加 sprite_file 指向 PNG（代码自动找同名 .tres）
4. 在 main.tscn 实例的 AnimatedSprite2D 上挂载 sprite_frames = .tres
5. 重启游戏验收
```

**禁止手写 .tres**（Godot 序列化文本格式严格，手写易错）。要改资源：
- 行布局/动画变了 → 改 `sprite_factory.gd`，重跑生成脚本
- 个别角色要差异化 → 在 Godot 编辑器里打开 `.tres` GUI 调

### 1.15 重构旧代码标记 ⚠️

P0 / P1 早期为了快速 demo 留了一些"野路子"，**已被本规范禁止**。后续 P2+ 应逐个迁移：

| 现存野路子 | 标准方案 | 优先级 |
|---|---|---|
| 主场景建筑用 ColorRect + Label | 做 Building 场景 + Sprite2D + StaticBody2D | 高 |
| 主场景地面用 ColorRect | TileMap + TileSet 像素地砖 | 高 |
| 玩家 NPC 直线 move_toward 寻路 | NavigationAgent2D + NavigationRegion2D | 中 |
| 动物头顶 Label 跟随 | 独立 CanvasLayer + 投影坐标 | 低 |
| 硬编码地点坐标 JSON | 主场景里 Marker2D 占位 + 编辑器拖动 | 低 |

修复时遵循"小步、可验证"原则，每改一类就跑一次验收。

---

## 二、AI 助手工作规则（强制）

### 2.1 新会话启动协议（强制，AI 自动执行）

**每次会话开始，AI 无需用户提醒，自动按顺序执行**：

1. 读 `.codemaker/PROJECT_RULES.md`（协议速查）
2. 读 `.codemaker/ROADMAP.md`（当前阶段 + Pending 任务）
3. 向用户简短确认："已读项目文档，当前 pending：[列出 ROADMAP 里未完成项]，继续 [最近任务]？"
4. 等用户回复后开始执行

> `.codemaker/DECISIONS.md` 遇到架构/选型问题时按需读，不必每次读。

### 2.1.1 自动更新文档协议（强制，AI 自动执行）

**以下三个时机 AI 必须主动更新 `ROADMAP.md` / `DECISIONS.md`，不等用户提醒**：

| 触发时机 | 动作 |
|----------|------|
| 完成 ROADMAP 里的一项任务 | 立刻把该项移到 ✅ 已完成，写交付清单 + 日期 |
| 做出新的架构/选型决定 | 追加到 `DECISIONS.md`，带日期和理由 |
| 上下文用量超 80% | 提醒用户开新 session，并更新 `ROADMAP.md` 写当前进度（包括"半完成"状态） |
| 用户说"结束/暂停/换会话" | 立刻把当前未完成工作写入 `ROADMAP.md` 的 🔄 进行中 区段 |

**写入 ROADMAP 的 ✅ 已完成 项必须含**：
- 任务名 + 完成日期
- 交付的文件列表（精确路径）
- 一句话说明做了什么

### 2.2 改代码前
1. **必须先 `read_file` 读现有完整内容**
2. 列"将改 X → Y"差异说明
3. 多文件改动 → 先列计划，等用户 ✅ 才执行
4. **禁止整文件重写**，除非用户明确同意

### 2.3 不擅自覆盖
- 用户已写的代码视为权威，不可随意删改
- 若认为有问题：先指出 + 给方案 + 等用户决定
- 重命名/移动文件前必须确认

### 2.4 计划确认机制
所有以下操作前**先输出计划等待 ✅**：
- 创建 ≥ 2 个文件
- 改动核心架构
- 安装新依赖
- Git 操作

### 2.5 增量交付
小步、可验证。每个 P 阶段结束保证项目能跑。每阶段完成后等用户验收再下一阶段。

### 2.6 工具调用克制
- 能用 `grep_search` 搞定就不用 `read_file` 全读
- 不必要不批量读文件
- 输出聚焦回答问题，不堆砌无关代码

---

## 三、命名约定速查

| 类别 | 规则 | 示例 |
|---|---|---|
| 场景文件 | snake_case.tscn | `animal.tscn` |
| 脚本文件 | snake_case.gd | `world_clock.gd` |
| JSON 数据 | snake_case.json | `bear_baker.json` |
| 节点名 | PascalCase | `NavigationAgent2D` |
| `class_name` | PascalCase | `class_name Animal` |
| 变量/函数 | snake_case | `move_speed`、`get_position()` |
| 信号 | snake_case 动词 | `dialog_finished`、`tick` |
| 常量 | UPPER_SNAKE | `MAX_SPEED` |
| 私有方法 | `_` 前缀 | `_update_state()` |

---

## 四、Git 规范

- 提交前 `git status` 看清改动
- commit 信息中文 OK，前缀：`feat: / fix: / refactor: / docs: / chore:`
- `.godot/` 目录、`.env`、`__pycache__/` 加 `.gitignore`

---

## 五、目录结构（参考）

```
game01/
├── .codemaker/
│   ├── PROJECT_RULES.md      ← 协议速查（新会话必读 1）
│   ├── ROADMAP.md            ← 阶段表 + pending（新会话必读 2）
│   ├── DECISIONS.md          ← 技术决策日志（按需读）
│   └── skills/
│       └── project_rules.md  ← 详细规范（本文件）
├── .gitignore
├── README.md
├── project.godot
├── icon.svg
├── scenes/
│   ├── main.tscn
│   ├── entities/
│   │   ├── player.tscn
│   │   └── animal.tscn
│   └── ui/
│       ├── dialog.tscn
│       ├── thought_bubble.tscn
│       └── clock_hud.tscn
├── scripts/
│   ├── world_clock.gd      (autoload)
│   ├── location_db.gd      (autoload)
│   ├── player.gd
│   ├── animal.gd
│   ├── dialog_ui.gd
│   ├── thought_bubble.gd
│   └── clock_hud.gd
├── data/
│   ├── animals/
│   │   ├── bear_baker.json
│   │   └── fox_postman.json
│   └── locations.json
├── assets/                 (P0 用色块，后期填充)
└── agent_server/           (P0-2 起)
    ├── main.py
    ├── agent.py
    ├── memory.py
    ├── llm.py
    ├── requirements.txt
    └── .env.example
```

---

## 六、关键设计决策记录

- **2026-05-23** 选 Godot 4.6.3、DeepSeek API、俯视 2D 动森风、色块占位起步
- **2026-05-23** 三阶段验收节奏（用户每阶段验收）
- **2026-05-23** 玩家可控角色（动森式），动物为 LLM agent

> 后续重要决策追加到本节，带日期。
