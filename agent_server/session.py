"""会话隔离：每个玩家一个独立世界（独立 SQLite 文件）。

设计：所有 store/manager 都是 DB 无状态包装（历史/好感/任务等全在 DB），
因此可作为共享单例；隔离只需两点——
  1. DB 连接目标按 ContextVar(db.current_db_path) 路由到 data/{sid}.db；
  2. 少量"每日去重"计数器随 Session 走（session_ctx）。
LLM 与 personas 全局共享。
"""
from __future__ import annotations

from contextvars import ContextVar
from pathlib import Path
from typing import Dict, Optional

import db

DATA_DIR = Path(__file__).parent / "data"


class Session:
    """单个玩家世界的运行时状态：db 路径 + 每日触发去重计数器。"""

    def __init__(self, session_id: str, db_path: Path) -> None:
        self.session_id = session_id
        self.db_path = db_path
        # 每日触发去重（原 main.py 模块级全局，改为按会话）
        self.last_reflect_day: int = -1
        self.last_election_recompute_day: int = -1
        self.last_opponent_action_slot: int = -1
        self.last_known_game_day: int = -1
        self.last_crisis_spawn_day: int = -1
        self.last_day_event_day: int = -1


# 当前正在处理的会话（供共享单例/闭包读取每日计数器）
session_ctx: ContextVar[Optional[Session]] = ContextVar("session_ctx", default=None)


class SessionRegistry:
    """按 session_id 惰性创建并缓存 Session，各自独立 DB 文件。"""

    def __init__(self) -> None:
        self._sessions: Dict[str, Session] = {}

    def get_or_create(self, session_id: Optional[str]) -> Session:
        # 无 sid（编辑器/旧客户端）→ 沿用默认 town.db，保持既有开发行为
        if not session_id:
            key = "__default__"
            path = db.DB_PATH
        else:
            key = session_id
            path = DATA_DIR / f"{session_id}.db"

        sess = self._sessions.get(key)
        if sess is not None:
            return sess

        sess = Session(key, path)
        db.init_schema(path)  # 幂等：同一路径只建一次
        self._sessions[key] = sess
        return sess


def bind(sess: Session) -> None:
    """把当前 asyncio 任务上下文绑定到该会话（DB 路由 + 计数器）。"""
    db.current_db_path.set(sess.db_path)
    session_ctx.set(sess)


def current() -> Optional[Session]:
    return session_ctx.get()
