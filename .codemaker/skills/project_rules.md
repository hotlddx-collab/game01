# Animal Town 项目开发规范

> **每次开发前 AI 必读**。新会话第一件事就是读这份文件。

---

## 项目概述

Agent 驱动的 2D 动物小镇游戏（动森风格）。差异化：动物由 LLM 驱动，比传统 NPC 聪明得多——能记住玩家、按性格回应、自主生活。

- **引擎**：Godot 4.6.3
- **视角**：俯视 2D（稍 45° 斜视），动森风
- **后端**（P1+）：Python FastAPI + WebSocket
- **LLM**：DeepSeek API（OpenAI 兼容格式）
- **记忆**：SQLite（P1 起）+ 向量检索（P2+）
- **美术**：色块占位起步，后期换 itch.io 素材或 AI 生成

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

## 一、Godot 开发规范（强制）

### 1.1 场景与节点
- **必须用场景（.tscn）组织实体**，禁止纯代码 `new` 节点拼装
- 每个可复用实体（Player/Animal/UI 组件）独立 `.tscn`
- 节点命名：**PascalCase**（`AnimalNPC`、`DialogPanel`）
- 脚本/场景文件名：**snake_case**（`animal.gd`、`dialog.tscn`）

### 1.2 节点选型
| 用途 | 节点 |
|---|---|
| 玩家/NPC 角色 | `CharacterBody2D` |
| NPC 寻路 | `NavigationAgent2D` + `NavigationRegion2D` |
| 触发交互 | `Area2D` + `CollisionShape2D` |
| UI 根 | `Control` 在独立 `CanvasLayer` |
| 全局服务 | autoload 单例（`WorldClock`、`LocationDB`） |
| 摄像机 | `Camera2D` 跟随玩家 |

### 1.3 必须 `@export` 的参数
所有"调试时可能要改的数值"全部 `@export`，给默认值 + 注释：

```gdscript
@export var move_speed: float = 100.0  # 像素/秒
@export var interact_radius: float = 32.0
@export_file("*.json") var persona_file: String
@export_range(0.1, 10.0, 0.1) var time_scale: float = 1.0
```

不可在脚本里写魔数（直接 `100.0`、`32.0`）。

### 1.4 通信用信号
组件间用 `signal`，避免硬引用。父子可用 `%UniqueName` 或 `@onready`。

```gdscript
signal dialog_finished(animal_id: String)
```

### 1.5 资源路径
- 一律 `res://` 绝对路径
- 目录约定：
  - `data/` — JSON 数据（persona、location）
  - `scenes/` — `.tscn`
  - `scripts/` — `.gd`
  - `assets/` — 美术、音频
  - `agent_server/` — Python 后端（P1 起）

### 1.6 GDScript 风格
- **类型标注必写**：`var hp: int = 100`
- **函数签名带类型**：`func get_pos() -> Vector2:`
- 常量大写：`const TICK_INTERVAL: float = 0.5`
- 用 `@onready` 拿节点引用
- 顶部按顺序：`@tool` → `class_name` → `extends` → `signal` → `enum` → `const` → `@export` → `var` → `@onready` → `_ready` → `_process` → 其他方法

### 1.7 数据驱动
- 动物性格、日程、地点坐标全部走 JSON / Resource，不写死在脚本
- 改一个动物的性格只改 JSON，不动代码

---

## 二、AI 助手工作规则（强制）

### 2.1 会话开始
**新会话第一件事**：读 `.codemaker/skills/project_rules.md`（本文件）。读完才能动手。

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
├── .codemaker/skills/project_rules.md   ← 本文件
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
