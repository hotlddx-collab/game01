"""SQLite 初始化与连接管理。"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


DB_PATH = Path(__file__).parent / "town.db"

_SCHEMA_SQL = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS memories (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  animal_id   TEXT NOT NULL,
  game_time   TEXT,
  real_time   INTEGER NOT NULL,
  type        TEXT NOT NULL,          -- 'dialog' | 'event' | 'reflection' | 'observation'
  speaker     TEXT,                   -- 'player' | 'self' | 'other:xxx'
  content     TEXT NOT NULL,
  importance  INTEGER DEFAULT 5,
  metadata    TEXT
);
CREATE INDEX IF NOT EXISTS idx_mem_animal_time ON memories(animal_id, real_time DESC);
CREATE INDEX IF NOT EXISTS idx_mem_importance  ON memories(animal_id, importance DESC);
CREATE INDEX IF NOT EXISTS idx_mem_type        ON memories(animal_id, type);

CREATE TABLE IF NOT EXISTS player_profile (
  animal_id   TEXT NOT NULL,
  key         TEXT NOT NULL,
  value       TEXT NOT NULL,
  updated_at  INTEGER NOT NULL,
  PRIMARY KEY (animal_id, key)
);

CREATE TABLE IF NOT EXISTS world_events (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  game_time   TEXT,
  real_time   INTEGER NOT NULL,
  location    TEXT,
  actor       TEXT,                   -- 'player' | animal_id
  description TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_world_time ON world_events(real_time DESC);

CREATE TABLE IF NOT EXISTS reflection_state (
  animal_id        TEXT PRIMARY KEY,
  last_reflect_at  INTEGER NOT NULL,
  last_memory_id   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS affection (
  animal_id       TEXT PRIMARY KEY,
  value           INTEGER NOT NULL DEFAULT 0,
  updated_at      INTEGER NOT NULL,
  last_greet_day  INTEGER NOT NULL DEFAULT -1
);

CREATE TABLE IF NOT EXISTS gift_log (
  animal_id      TEXT NOT NULL,
  item_id        TEXT NOT NULL,
  count          INTEGER NOT NULL DEFAULT 0,
  last_gift_day  INTEGER NOT NULL DEFAULT -1,
  updated_at     INTEGER NOT NULL,
  PRIMARY KEY (animal_id, item_id)
);
"""


def _migrate(conn) -> None:
    """SQLite 简易迁移：CREATE TABLE IF NOT EXISTS 不会加新列，旧库需 ALTER。"""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(affection)").fetchall()}
    if "last_greet_day" not in cols:
        conn.execute("ALTER TABLE affection ADD COLUMN last_greet_day INTEGER NOT NULL DEFAULT -1")

_lock = threading.Lock()
_initialized = False


def init_schema() -> None:
    """启动时调用。建表、迁移。"""
    global _initialized
    with _lock:
        if _initialized:
            return
        with _connect() as conn:
            conn.executescript(_SCHEMA_SQL)
            _migrate(conn)
            conn.commit()
        _initialized = True


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """短连接，with 自动提交关闭。"""
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=5.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
