"""SQLite 存储层（agenttrace/storage，FR-3.1）。

职责（AT-DES-001 §3.2，DDL 定稿）：
- DDL 建表（traces/spans 两表 + 索引，PRAGMA WAL/synchronous=NORMAL）；
- 按 PRAGMA user_version 增量迁移（本期 v1）；
- 单事务批量 UPSERT（占位 INSERT 与终态 UPDATE 合并落盘，决策 #4/#6）；
- 连接管理：带 check_same_thread=False，但按设计仅由 flusher 单线程独占写。

Sprint 1 任务 3（TDD：tests/test_storage.py 先行）。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

_DDL_V1 = """
CREATE TABLE IF NOT EXISTS traces (
  id           TEXT PRIMARY KEY,
  name         TEXT NOT NULL,
  session_id   TEXT,
  status       TEXT NOT NULL DEFAULT 'running',
  start_time   TEXT NOT NULL,
  end_time     TEXT,
  total_tokens INTEGER NOT NULL DEFAULT 0,
  latency_ms   INTEGER,
  error        TEXT,
  created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_traces_start  ON traces(start_time DESC);
CREATE INDEX IF NOT EXISTS idx_traces_status ON traces(status);

CREATE TABLE IF NOT EXISTS spans (
  id             TEXT PRIMARY KEY,
  trace_id       TEXT NOT NULL REFERENCES traces(id),
  parent_id      TEXT,
  type           TEXT NOT NULL,
  name           TEXT NOT NULL,
  input          TEXT,
  output         TEXT,
  status         TEXT NOT NULL DEFAULT 'running',
  status_message TEXT,
  model          TEXT,
  token_in       INTEGER NOT NULL DEFAULT 0,
  token_out      INTEGER NOT NULL DEFAULT 0,
  start_time     TEXT NOT NULL,
  end_time       TEXT,
  duration_ms    INTEGER,
  is_stream      INTEGER NOT NULL DEFAULT 0,
  created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_spans_trace  ON spans(trace_id);
CREATE INDEX IF NOT EXISTS idx_spans_parent ON spans(parent_id);
"""

# 终态字段（status/end_time 等）随占位更新覆盖；标识字段（name/type 等）首次落盘后不变
_UPSERT_TRACE = """
INSERT INTO traces (id, name, session_id, status, start_time, end_time,
                    total_tokens, latency_ms, error, created_at)
VALUES (:id, :name, :session_id, :status, :start_time, :end_time,
        :total_tokens, :latency_ms, :error, :created_at)
ON CONFLICT(id) DO UPDATE SET
    status       = excluded.status,
    end_time     = excluded.end_time,
    total_tokens = excluded.total_tokens,
    latency_ms   = excluded.latency_ms,
    error        = excluded.error
"""

_UPSERT_SPAN = """
INSERT INTO spans (id, trace_id, parent_id, type, name, input, output, status,
                   status_message, model, token_in, token_out, start_time, end_time,
                   duration_ms, is_stream, created_at)
VALUES (:id, :trace_id, :parent_id, :type, :name, :input, :output, :status,
        :status_message, :model, :token_in, :token_out, :start_time, :end_time,
        :duration_ms, :is_stream, :created_at)
ON CONFLICT(id) DO UPDATE SET
    output         = excluded.output,
    status         = excluded.status,
    status_message = excluded.status_message,
    token_in       = excluded.token_in,
    token_out      = excluded.token_out,
    end_time       = excluded.end_time,
    duration_ms    = excluded.duration_ms
"""


class Storage:
    """SQLite 写侧存储。查询（SELECT）由 server 侧另行只读访问（M3/垂直切片）。"""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._migrate()

    def _migrate(self) -> None:
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if version < 1:
            self._conn.executescript(_DDL_V1)
            self._conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            self._conn.commit()
        # 后续版本按 user_version 递增迁移，保持已发布库可升级

    def write_batch(
        self,
        traces: Iterable[Mapping[str, Any]],
        spans: Iterable[Mapping[str, Any]],
    ) -> None:
        """单事务批量 UPSERT：本批 trace 行与 span 行原子落盘（决策 #6）。"""
        with self._conn:
            self._conn.executemany(_UPSERT_TRACE, list(traces))
            self._conn.executemany(_UPSERT_SPAN, list(spans))

    def close(self) -> None:
        self._conn.close()
