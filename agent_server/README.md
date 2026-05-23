# Agent Server

P0-2 起的 Python 后端：FastAPI + WebSocket + DeepSeek LLM。

## 启动

### 1. 配 API Key
```bash
cd agent_server
cp .env.example .env
# 编辑 .env，把 DEEPSEEK_API_KEY 改成你自己的（重要：旧的已泄露请重新生成）
```

### 2. 装依赖（建议虚拟环境）
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. 跑
```bash
python main.py
# 应看到：
# [INFO] 加载 personas: ['bear_baker', 'fox_postman']
# [INFO] listening on ws://127.0.0.1:8765/ws
```

健康检查：浏览器访问 http://127.0.0.1:8765/ → 看到 `{"status":"ok","animals":[...]}`

### 4. 再开 Godot 跑游戏

游戏端会自动连 `ws://127.0.0.1:8765/ws`。

## 协议

**客户端 → 服务器**：
```json
{"type": "greet", "animal_id": "bear_baker", "context": {"time": "08:30", "location": "bakery", "location_label": "面包店", "intent": "去烤面包"}}
{"type": "chat",  "animal_id": "bear_baker", "user_text": "你好啊", "context": {...}}
{"type": "reset", "animal_id": "bear_baker"}
```

**服务器 → 客户端**：
```json
{"type": "reply", "animal_id": "bear_baker", "text": "...", "ok": true}
{"type": "ok",    "animal_id": "bear_baker"}
{"type": "error", "message": "..."}
```

## 当前限制（P0-2）

- 历史仅内存，重启丢
- 单进程单 worker
- 无认证（仅本地开发）
- 无日志持久化
