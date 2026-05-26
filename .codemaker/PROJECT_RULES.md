# 怪物森林 协议速查

> **AI 每次会话自动读此文件 + ROADMAP.md，无需用户提醒。**  
> 详细规范见 `.codemaker/skills/project_rules.md`。

---

## 技术栈

- Godot 4.6.3 | Python FastAPI + WebSocket | DeepSeek API | SQLite
- 俯视 2D 动森风 | 像素 chibi 风（64×64 帧）

## 目录约定

```
scenes/entities/    NPC/Player .tscn
scenes/ui/          UI 组件 .tscn
scripts/            .gd 脚本
assets/characters/  PNG + .tres SpriteFrames
data/animals/       NPC persona JSON
tools/              @tool EditorScript
agent_server/       Python 后端
.codemaker/         项目文档（不入游戏）
```

## 核心禁止事项

- ❌ 运行时 `SpriteFrames.new()` → 用 Python 生成 .tres
- ❌ `get_node("../../sibling")` → `%UniqueName` 或 `@onready`
- ❌ 硬编码坐标 → Marker2D 占位
- ❌ `Variant` 类型 → 明确类型标注
- ❌ 代码里构造 Theme/TileSet → .tres 资源文件

## WebSocket 消息类型

| type | 方向 | 说明 |
|------|------|------|
| `dialog` | C→S | 玩家发起 NPC 对话 |
| `dialog_reply` | S→C | NPC 回复玩家 |
| `npc_chat` | C→S | 触发两 NPC 互动 |
| `npc_chat_reply` | S→C | NPC 互动结果 |

## Autoload 单例

`WorldClock` / `LocationDB` / `ChatManager` / `AgentClient`

## 当前 Pending 任务

详见 `.codemaker/ROADMAP.md`。简要：
- F（进行中）：老咸 + 煊赫 NPC — 生成 .tres + JSON + 加入 main.tscn
- B：好感度 + emote 气泡
- D：玩家偷听 NPC 对话
- E：双向 NPC 对话 2-3 轮
