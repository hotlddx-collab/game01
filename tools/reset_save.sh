#!/bin/bash
# 删档重开：清掉 SQLite 持久数据，下次启动 server 会重建空表
# 用法：bash tools/reset_save.sh

set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"

# 杀掉运行中的后端（如果有）
PID=$(lsof -tiTCP:8765 -sTCP:LISTEN 2>/dev/null || true)
if [ -n "$PID" ]; then
    echo "停掉运行中的后端 PID=$PID"
    kill "$PID" 2>/dev/null || true
    sleep 1
fi

# 删档
rm -f "$ROOT/agent_server/town.db" "$ROOT/agent_server/town.db-shm" "$ROOT/agent_server/town.db-wal"
echo "✅ 已清空 town.db / -shm / -wal"
echo ""
echo "下一步："
echo "  cd $ROOT/agent_server && python3 main.py"
echo "  然后在 Godot 重新跑游戏"
