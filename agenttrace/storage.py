"""SQLite 存储层（agenttrace/storage，FR-3.1）。

职责（AT-DES-001 §3.2，DDL 定稿）：
- DDL 建表（traces/spans 两表 + 索引，WAL；默认 FULL，可选 async 用 NORMAL）；
- 按 PRAGMA user_version 增量迁移（本期 v1）；
- 单事务批量 UPSERT（占位 INSERT 与终态 UPDATE 合并落盘，决策 #4/#6）；
- 连接管理：check_same_thread=False；durable 由管线写锁串行化，async 由 flusher 独占写。

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
    model          = excluded.model,
    status         = excluded.status,
    status_message = excluded.status_message,
    token_in       = excluded.token_in,
    token_out      = excluded.token_out,
    end_time       = excluded.end_time,
    duration_ms    = excluded.duration_ms
"""


class Storage:
    """SQLite 写侧存储。查询（SELECT）由 server 侧另行只读访问（M3/垂直切片）。"""

    def __init__(self, db_path: str | Path, *, durable: bool = True) -> None:
        self.db_path = str(db_path)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=0.2)
        try:
            self._migrate()
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=" + ("FULL" if durable else "NORMAL"))
        except Exception:
            self._conn.close()
            raise

    def _migrate(self) -> None:
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise sqlite3.DatabaseError("unsupported schema version")
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
        trace_rows, span_rows = list(traces), list(spans)
        with self._conn:
            self._conn.executemany(_UPSERT_TRACE, trace_rows)
            span_ids = {row["id"] for row in span_rows}
            previous = self._span_tokens(span_ids)
            self._conn.executemany(_UPSERT_SPAN, span_rows)
            current = self._span_tokens(span_ids)
            deltas: dict[str, int] = {}
            for sign, rows in ((-1, previous), (1, current)):
                for trace_id, tokens in rows.values():
                    deltas[trace_id] = deltas.get(trace_id, 0) + sign * tokens
            replaced = {row["id"] for row in trace_rows}
            # 普通 span 只更新已提交用量的差值，避免 N 个步骤触发 O(N²) 全树扫描。
            # 同批 Trace UPSERT 覆盖过聚合值时，仍以库内实际数据重算。
            for trace_id, delta in deltas.items():
                if trace_id in replaced:
                    self._conn.execute(
                        "UPDATE traces SET total_tokens="
                        "(SELECT COALESCE(SUM(token_in+token_out),0) "
                        "FROM spans WHERE trace_id=traces.id) WHERE id=?", (trace_id,),
                    )
                elif delta:
                    self._conn.execute(
                        "UPDATE traces SET total_tokens=total_tokens+? WHERE id=?",
                        (delta, trace_id),
                    )

    def _span_tokens(self, ids: set[str]) -> dict[str, tuple[str, int]]:
        tokens = {}
        ordered = list(ids)
        # 兼容 SQLite 较低的绑定参数上限，大批量导入也不拼接事件内容。
        for offset in range(0, len(ordered), 500):
            batch = ordered[offset:offset + 500]
            placeholders = ",".join("?" for _ in batch)
            rows = self._conn.execute(
                "SELECT id,trace_id,token_in+token_out FROM spans "
                f"WHERE id IN ({placeholders})", batch,
            )
            tokens.update((span_id, (trace_id, total)) for span_id, trace_id, total in rows)
        return tokens

    def close(self) -> None:
        self._conn.close()
