"""SQLite 初始化与连接管理。"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Iterator, Optional


DB_PATH = Path(__file__).parent / "town.db"

# 当前连接使用的 DB 文件路径（按会话隔离）。默认指向 town.db，
# 编辑器单会话开发行为不变；每个玩家连接时切到各自的 data/{sid}.db。
current_db_path: ContextVar[Path] = ContextVar("current_db_path", default=DB_PATH)

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

CREATE TABLE IF NOT EXISTS reflections (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  animal_id   TEXT NOT NULL,
  game_day    INTEGER NOT NULL,
  content     TEXT NOT NULL,
  importance  INTEGER NOT NULL DEFAULT 5,
  tags        TEXT,
  created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_refl_animal_day ON reflections(animal_id, game_day DESC);

CREATE TABLE IF NOT EXISTS animal_intents (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  animal_id     TEXT NOT NULL,       -- 发起意图的 NPC
  target_id     TEXT,                -- 目标 NPC id（可为空）
  intent_text   TEXT NOT NULL,       -- 原始意图文本（反思原句）
  game_day      INTEGER NOT NULL,    -- 产生于第几天
  activate_hour INTEGER NOT NULL DEFAULT 10,  -- 几点激活（游戏时间）
  consumed      INTEGER NOT NULL DEFAULT 0,   -- 0=待执行, 1=已执行
  created_at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_intent_day ON animal_intents(game_day, consumed);

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

-- 镇长选举：任期表
CREATE TABLE IF NOT EXISTS election_term (
  term_id     INTEGER PRIMARY KEY AUTOINCREMENT,
  start_day   INTEGER NOT NULL,
  end_day     INTEGER,                 -- NULL 表示进行中
  winner_id   TEXT,                    -- 'player' | npc_id（结算后写入）
  opponent_id TEXT NOT NULL,           -- 当期对手 NPC id
  result_json TEXT,                    -- 结算快照（票数/breakdown）
  created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_term_active ON election_term(end_day);

-- 镇长选举：每日 NPC 投票权重快照（每日 22:00 重算后写入）
CREATE TABLE IF NOT EXISTS election_weight (
  term_id        INTEGER NOT NULL,
  game_day       INTEGER NOT NULL,
  voter_id       TEXT NOT NULL,         -- NPC id
  candidate_id   TEXT NOT NULL,         -- 'player' | npc_id
  weight         REAL NOT NULL,
  breakdown_json TEXT,                   -- 各子项明细，调试用
  PRIMARY KEY (term_id, game_day, voter_id, candidate_id)
);

-- 镇长选举：候选人当期状态
CREATE TABLE IF NOT EXISTS candidate_state (
  term_id          INTEGER NOT NULL,
  candidate_id     TEXT NOT NULL,         -- 'player' | npc_id
  is_incumbent     INTEGER NOT NULL DEFAULT 0,
  power_points     INTEGER NOT NULL DEFAULT 0,
  power_points_max INTEGER NOT NULL DEFAULT 3,
  last_power_day   INTEGER NOT NULL DEFAULT -1,  -- 上次发放权力点的游戏日（跨日重置用）
  platform_json    TEXT,                  -- LLM 生成的竞选纲领
  PRIMARY KEY (term_id, candidate_id)
);

-- 镇长选举：对手每日动作日志
CREATE TABLE IF NOT EXISTS opponent_actions (
  action_id              INTEGER PRIMARY KEY AUTOINCREMENT,
  term_id                INTEGER NOT NULL,
  game_day               INTEGER NOT NULL,
  candidate_id           TEXT NOT NULL,         -- 对手 NPC id
  action_type            TEXT NOT NULL,         -- 'visit'|'smear'|'promise'|'speech'
  target_npc             TEXT,                  -- 动作的目标 NPC（可空）
  llm_text               TEXT,                  -- LLM 生成的台词
  mechanical_effect_json TEXT,                  -- 机械效果（影响哪些子项）
  created_at             INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_opp_term_day ON opponent_actions(term_id, game_day);

-- 镇长选举：玩家承诺池（复用 quest 作为内容载体）
CREATE TABLE IF NOT EXISTS promises (
  promise_id    INTEGER PRIMARY KEY AUTOINCREMENT,
  term_id       INTEGER NOT NULL,
  candidate_id  TEXT NOT NULL,            -- 'player'（D6 暂只玩家承诺；后续可扩对手）
  npc_id        TEXT NOT NULL,            -- 承诺受益方
  quest_id      TEXT NOT NULL,            -- 关联 quests.json 条目
  status        TEXT NOT NULL DEFAULT 'pending',  -- pending|fulfilled|broken
  accept_day    INTEGER NOT NULL,
  deadline_day  INTEGER,                  -- NULL = 任期末截止
  resolved_day  INTEGER,
  created_at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_promise_term ON promises(term_id, status);
CREATE INDEX IF NOT EXISTS idx_promise_quest ON promises(quest_id);

-- 镇长选举：辩论日（D6）每个 NPC 对候选人立场的打分（接 weight 的 debate 子项）
CREATE TABLE IF NOT EXISTS debate_scores (
  term_id    INTEGER NOT NULL,
  voter_id   TEXT NOT NULL,            -- 旁听/提问 NPC id
  candidate_id TEXT NOT NULL,          -- 'player' | 对手 npc_id
  score      REAL NOT NULL,            -- 该 NPC 对该候选人本场辩论的好评分 [-0.5,1.0]
  detail_json TEXT,                    -- 立场匹配明细
  created_at INTEGER NOT NULL,
  PRIMARY KEY (term_id, voter_id, candidate_id)
);

-- 危机调解：镇上 NPC 间纠纷事件，玩家介入断案
CREATE TABLE IF NOT EXISTS crisis_state (
  crisis_id       INTEGER PRIMARY KEY AUTOINCREMENT,
  template_id     TEXT NOT NULL,       -- data/world/crises.json 条目
  game_day        INTEGER NOT NULL,
  status          TEXT NOT NULL DEFAULT 'active',  -- active|resolved
  chosen_option   TEXT,
  statements_json TEXT,                -- 缓存双方 LLM 说法
  deadline_hour   INTEGER,             -- 软性截止：绝对游戏小时(day*24+hour)，超时自动按「不管」结算
  created_at      INTEGER NOT NULL,
  resolved_at     INTEGER
);
CREATE INDEX IF NOT EXISTS idx_crisis_status ON crisis_state(status);
CREATE INDEX IF NOT EXISTS idx_crisis_template ON crisis_state(template_id, status);

-- 心情系统：每只 NPC 的动态情绪（单标量 valence，随时间向 0 衰减）
CREATE TABLE IF NOT EXISTS mood (
  animal_id     TEXT PRIMARY KEY,
  value         INTEGER NOT NULL DEFAULT 0,   -- [-100,100]，正=愉悦 负=低落/烦躁
  updated_at    INTEGER NOT NULL,
  last_day      INTEGER NOT NULL DEFAULT -1   -- 上次结算所在游戏日（惰性衰减用）
);

-- 八卦话题：小镇流传的话题（可真可假，有情感倾向与热度）
CREATE TABLE IF NOT EXISTS rumor (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  subject_id  TEXT NOT NULL,                   -- 话题主角：animal_id 或 'player'
  sentiment   TEXT NOT NULL DEFAULT 'neutral', -- praise 褒 / smear 贬 / neutral 中
  truth       INTEGER NOT NULL DEFAULT 1,      -- 1 真 0 假
  heat        INTEGER NOT NULL DEFAULT 50,     -- 热度 0-100
  content     TEXT NOT NULL,                   -- 原始话题一句话
  origin      TEXT NOT NULL DEFAULT '',        -- 来源：player / auto / npc_id
  involved_ids TEXT NOT NULL DEFAULT '',       -- 除主角外被卷进内容里的 NPC，逗号分隔。
                                               -- 当事人不该把自己参与的事当八卦讲出去。
  game_day    INTEGER NOT NULL DEFAULT 0,
  status      TEXT NOT NULL DEFAULT 'active',  -- active / faded / debunked
  created_at  INTEGER NOT NULL,
  updated_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rumor_status ON rumor(status, heat);
CREATE INDEX IF NOT EXISTS idx_rumor_subject ON rumor(subject_id, status);

-- 谁知道哪条八卦 + ta 口中的版本（每传一手可能变味）
CREATE TABLE IF NOT EXISTS rumor_knowledge (
  rumor_id    INTEGER NOT NULL,
  animal_id   TEXT NOT NULL,
  version     TEXT NOT NULL,                   -- 该 NPC 口中的说法
  hops        INTEGER NOT NULL DEFAULT 0,      -- 传到 ta 手上经过几手
  learned_day INTEGER NOT NULL DEFAULT 0,
  told_count  INTEGER NOT NULL DEFAULT 0,      -- ta 传给别人几次（防刷）
  created_at  INTEGER NOT NULL,
  PRIMARY KEY (rumor_id, animal_id)
);
CREATE INDEX IF NOT EXISTS idx_rumor_know_animal ON rumor_knowledge(animal_id);

-- 谁信了哪条八卦：每人每条只判定一次（判定即锁定），决定是否影响选情
CREATE TABLE IF NOT EXISTS rumor_belief (
  rumor_id    INTEGER NOT NULL,
  animal_id   TEXT NOT NULL,
  state       TEXT NOT NULL,                   -- believed 信 / rejected 不信
  source_id   TEXT NOT NULL DEFAULT '',        -- 从谁那听来（player / animal_id）
  score       REAL NOT NULL DEFAULT 0,         -- 当次信任分（调试用）
  judged_day  INTEGER NOT NULL DEFAULT 0,
  created_at  INTEGER NOT NULL,
  PRIMARY KEY (rumor_id, animal_id)
);
CREATE INDEX IF NOT EXISTS idx_rumor_belief_state ON rumor_belief(animal_id, state);

-- 任务状态（原由 quests.py 自建，并入 schema 以便每个会话库都有）
CREATE TABLE IF NOT EXISTS quests_state (
  quest_id       TEXT PRIMARY KEY,
  state          TEXT NOT NULL,
  schema_version INTEGER NOT NULL DEFAULT 1,
  accepted_at    INTEGER NOT NULL,
  completed_at   INTEGER,
  progress       INTEGER NOT NULL DEFAULT 0
);

-- 好感里程碑解锁记录（原由 milestones.py 自建）
CREATE TABLE IF NOT EXISTS milestones_unlocked (
  animal_id   TEXT NOT NULL,
  transition  TEXT NOT NULL,
  unlocked_at INTEGER NOT NULL,
  PRIMARY KEY (animal_id, transition)
);

-- 镇长政务任务：现任镇长期间随机刷新，玩家在 NPC 对话里指派执行
CREATE TABLE IF NOT EXISTS mayor_task (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  task_type    TEXT NOT NULL,          -- subdue_drunk|repair_sewer|cure_epidemic|clean|archive
  status       TEXT NOT NULL DEFAULT 'open',   -- open|assigned|resolved
  target_id    TEXT,                   -- 相关 NPC（酒鬼/病人主角）或空
  executor_id  TEXT,                   -- 被指派执行者：npc_id | 'player'
  method       TEXT,                   -- persuade|reason|threat
  outcome      TEXT,                   -- botch|ok|great
  spawn_day    INTEGER NOT NULL,
  created_at   INTEGER NOT NULL,
  resolved_at  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_mayor_task_status ON mayor_task(status);

-- NPC 之间的亲疏关系：连续值 [-100, 100]，取代原先的布尔好友集合。
-- 无序对（a < b 规范化存储），初值来自 relations.py 的 INITIAL_TIES。
CREATE TABLE IF NOT EXISTS npc_relation (
  a_id       TEXT NOT NULL,
  b_id       TEXT NOT NULL,
  value      INTEGER NOT NULL DEFAULT 0,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (a_id, b_id)
);
"""


def _migrate(conn) -> None:
    """SQLite 简易迁移：CREATE TABLE IF NOT EXISTS 不会加新列，旧库需 ALTER。"""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(affection)").fetchall()}
    if "last_greet_day" not in cols:
        conn.execute("ALTER TABLE affection ADD COLUMN last_greet_day INTEGER NOT NULL DEFAULT -1")
    # candidate_state.last_power_day（D9 权力点跨日重置）
    cs_cols = {row["name"] for row in conn.execute("PRAGMA table_info(candidate_state)").fetchall()}
    if cs_cols and "last_power_day" not in cs_cols:
        conn.execute("ALTER TABLE candidate_state ADD COLUMN last_power_day INTEGER NOT NULL DEFAULT -1")
    # crisis_state.deadline_hour（危机软性截止）
    cr_cols = {row["name"] for row in conn.execute("PRAGMA table_info(crisis_state)").fetchall()}
    if cr_cols and "deadline_hour" not in cr_cols:
        conn.execute("ALTER TABLE crisis_state ADD COLUMN deadline_hour INTEGER")
    # rumor.involved_ids（话题里被卷进来的当事人，防止 NPC 把自己的事当八卦讲）
    ru_cols = {row["name"] for row in conn.execute("PRAGMA table_info(rumor)").fetchall()}
    if ru_cols and "involved_ids" not in ru_cols:
        conn.execute("ALTER TABLE rumor ADD COLUMN involved_ids TEXT NOT NULL DEFAULT ''")

_lock = threading.Lock()
_initialized_paths: set[str] = set()


def init_schema(db_path: Optional[Path] = None) -> None:
    """建表 + 迁移。可指定 db 文件（按会话隔离）；同一路径只初始化一次。"""
    path = Path(db_path) if db_path is not None else current_db_path.get()
    key = str(path)
    with _lock:
        if key in _initialized_paths:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=5.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            conn.executescript(_SCHEMA_SQL)
            _migrate(conn)
            conn.commit()
        finally:
            conn.close()
        _initialized_paths.add(key)


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """短连接，with 自动提交关闭。连接目标由 current_db_path 决定。"""
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
    conn = sqlite3.connect(str(current_db_path.get()), timeout=5.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
