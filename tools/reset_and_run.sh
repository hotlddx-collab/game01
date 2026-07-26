#!/bin/bash
# 删库 + 重启后端：一条命令清空存档并在前台启动 server
# 用法：bash tools/reset_and_run.sh
#   保留存档只重启：RESET=0 bash tools/reset_and_run.sh

set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
SERVER="$ROOT/agent_server"
PY="$SERVER/.venv/bin/python"

# 杀掉运行中的后端（占用 8765 端口）
PID=$(lsof -tiTCP:8765 -sTCP:LISTEN 2>/dev/null || true)
if [ -n "$PID" ]; then
    echo "停掉运行中的后端 PID=$PID"
    kill "$PID" 2>/dev/null || true
    sleep 1
fi

# 删档（RESET=0 可跳过，仅重启）
if [ "${RESET:-1}" != "0" ]; then
    # 默认库（无 sid 的编辑器/旧客户端）
    rm -f "$SERVER/town.db" "$SERVER/town.db-shm" "$SERVER/town.db-wal"
    # 会话分库：实机客户端按 ?sid= 路由到 data/{sid}.db，才是真正的存档
    rm -f "$SERVER"/data/*.db "$SERVER"/data/*.db-shm "$SERVER"/data/*.db-wal 2>/dev/null || true
    echo "✅ 已清空 town.db 及 data/*.db（含 -shm/-wal 会话分库）"
else
    echo "↩️  保留存档，仅重启"
fi

# 选 python：优先 venv，缺失则回退系统 python3
if [ ! -x "$PY" ]; then
    echo "⚠️  未找到 $PY，回退 python3"
    PY="python3"
fi

echo "🚀 启动后端：$PY main.py"
cd "$SERVER" && exec "$PY" main.py
