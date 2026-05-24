# 怪物森林

Agent 驱动的 2D 怪物森林游戏（动森风格）。住满奇形怪状但和善的怪物居民，玩家是误入森林的旅人。

> **当前阶段**：P1 像素美术接入。

## 跑起来

1. 安装 [Godot 4.6.x](https://godotengine.org/download)
2. 打开 Godot → **导入** → 选 `project.godot` → 导入并编辑
3. 按 **F5** 运行（首次会让你确认主场景，已在 `project.godot` 配好 `res://scenes/main.tscn`）

## 操作

| 按键 | 作用 |
|---|---|
| W A S D / 方向键 | 玩家移动 |
| E | 与最近的动物对话；对话中按 E 关闭 / 加速打字 |

## 当前能体验到啥

- 4 个色块建筑：小熊家、狐狸家、面包店、邮局、广场
- 玩家小蓝人，摄像机跟随
- 小熊（棕）、狐狸（橙）按 JSON 日程在地点间行走
- 头顶气泡显示当前意图（如 "去烤面包"）
- 屏幕右上角游戏时间（默认 1 实秒 = 1 游戏分钟）
- 走近动物按 E 弹对话（占位文本，**P0-2 接 LLM 后变真智能**）

## 调参

进 Godot Inspector，选中节点改 `@export` 参数：
- `WorldClock`（autoload）：`time_scale`、`start_hour`、`tick_interval_minutes`
- `Player`：`move_speed`、`interact_radius`
- `Animal`：`move_speed`、`persona_file`

## 目录

```
scenes/        游戏场景 (.tscn)
scripts/       逻辑脚本 (.gd)
data/          动物 persona、地点配置 (.json)
assets/        美术（待填）
agent_server/  Python 后端（P0-2 起）
.codemaker/skills/project_rules.md   开发规范
```

## 路线

- [x] P0-1 Godot 骨架
- [ ] P0-2 Python 后端 + DeepSeek LLM 对话
- [ ] P0-3 短期记忆 + 昼夜滤镜
- [ ] P1+ 多动物、关系网、地图扩展
