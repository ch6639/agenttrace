"""标准库只读查询；SQL 排序列用白名单，过滤值均参数化。"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from agenttrace.storage import SCHEMA_VERSION


class QueryStore:
    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path).resolve()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=1)
        conn.row_factory = sqlite3.Row
        try:
            if conn.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
                raise sqlite3.DatabaseError("unsupported schema version")
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _where(status=None, t_from=None, t_to=None):
        clauses, values = [], []
        for column, op, value in (("status", "=", status), ("start_time", ">=", t_from),
                                  ("start_time", "<=", t_to)):
            if value is not None:
                clauses.append(f"t.{column} {op} ?")
                values.append(value)
        return " WHERE " + " AND ".join(clauses) if clauses else "", values

    def list_traces(self, *, status=None, t_from=None, t_to=None, order="start_time",
                    direction="desc", limit=50, offset=0):
        columns = {"start_time": "start_time", "latency": "latency_ms", "tokens": "total_tokens"}
        if order not in columns or direction not in ("asc", "desc"):
            raise ValueError("invalid sort")
        if not 1 <= limit <= 1000 or offset < 0:
            raise ValueError("invalid pagination")
        if not self.path.exists():
            return {"total": 0, "items": []}
        where, values = self._where(status, t_from, t_to)
        with self.connect() as conn:
            conn.execute("BEGIN")  # count 与分页内容使用同一个只读快照
            total = conn.execute("SELECT COUNT(*) FROM traces t" + where, values).fetchone()[0]
            rows = conn.execute(
                "SELECT t.id,t.name,t.status,t.start_time,t.latency_ms,t.total_tokens,"
                "(SELECT COUNT(*) FROM spans s WHERE s.trace_id=t.id) AS span_count "
                "FROM traces t" + where + f" ORDER BY t.{columns[order]} {direction}, t.id ASC"
                + " LIMIT ? OFFSET ?", [*values, limit, offset],
            ).fetchall()
            return {"total": total, "items": [dict(row) for row in rows]}

    def get_trace(self, trace_id):
        if not self.path.exists():
            return None
        with self.connect() as conn:
            conn.execute("BEGIN")
            trace = conn.execute("SELECT * FROM traces WHERE id=?", (trace_id,)).fetchone()
            if trace is None:
                return None
            spans = []
            for row in conn.execute(
                "SELECT * FROM spans WHERE trace_id=? ORDER BY start_time, rowid", (trace_id,)
            ):
                span = dict(row)
                for field in ("input", "output"):
                    if span[field] is not None:
                        try:
                            span[field] = json.loads(span[field])
                        except (ValueError, TypeError):
                            pass
                span["is_stream"] = bool(span["is_stream"])
                spans.append(span)
            return {"trace": dict(trace), "spans": spans}

    def stats(self, *, t_from=None, t_to=None):
        empty = {"total_traces": 0, "success_rate": 0, "total_tokens": 0,
                 "avg_steps": 0, "failure_types": {}}
        if not self.path.exists():
            return empty
        where, values = self._where(t_from=t_from, t_to=t_to)
        with self.connect() as conn:
            conn.execute("BEGIN")
            row = conn.execute(
                "SELECT COUNT(*) AS total, SUM(status='success') AS successes, "
                "SUM(status IN ('success','failed')) AS finished, SUM(total_tokens) AS tokens, "
                "AVG((SELECT COUNT(*) FROM spans s WHERE s.trace_id=t.id)) AS steps "
                "FROM traces t" + where, values,
            ).fetchone()
            failures = {}
            for failure in conn.execute("SELECT error FROM traces t" + where
                                        + (" AND " if where else " WHERE ")
                                        + "status='failed'", values):
                kind = (failure["error"] or "UnknownError").split(":", 1)[0]
                failures[kind] = failures.get(kind, 0) + 1
            return {"total_traces": row["total"],
                    "success_rate": (row["successes"] or 0) / (row["finished"] or 1),
                    "total_tokens": row["tokens"] or 0, "avg_steps": row["steps"] or 0,
                    "failure_types": failures}
