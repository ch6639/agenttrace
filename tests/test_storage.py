"""storage 模块测试（Sprint 1 任务 3，TDD 先行）。

覆盖：DDL/迁移与 WAL（AT-DES-001 §3.2）、单事务批量 UPSERT、
占位→终态更新语义、span 父子树、重启后数据仍可查（TC-09 内核验证）。
"""

import sqlite3

import pytest

from agenttrace.model import Span, Trace, dumps
from agenttrace.storage import SCHEMA_VERSION, Storage


def connect_raw(db_path: object) -> sqlite3.Connection:
    return sqlite3.connect(str(db_path))


def test_init_creates_schema_wal_and_version(tmp_path: object) -> None:
    db = tmp_path / "trace.db"
    Storage(db)
    conn = connect_raw(db)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"traces", "spans"} <= tables
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    finally:
        conn.close()


def test_reopen_keeps_data_tc09(tmp_path: object) -> None:
    db = tmp_path / "trace.db"
    s = Storage(db)
    t = Trace(name="tutor", status="success")
    sp = Span(trace_id=t.id, parent_id=None, type="tool", name="search")
    s.write_batch([t.to_row()], [sp.to_row()])
    s.close()

    s2 = Storage(db)  # TC-09：进程重启（新实例）后 trace 仍可查
    conn = connect_raw(db)
    try:
        assert conn.execute("SELECT count(*) FROM traces").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM spans").fetchone()[0] == 1
    finally:
        conn.close()
        s2.close()


def test_upsert_updates_terminal_fields_only(tmp_path: object) -> None:
    s = Storage(tmp_path / "trace.db")
    span = Span(trace_id="t", parent_id=None, type="llm", name="ask")
    s.write_batch([], [span.to_row()])  # 占位落盘

    span.finish("success", output=dumps("答案"), token_in=10, token_out=20)
    s.write_batch([], [span.to_row()])  # 终态写回

    conn = connect_raw(s.db_path)
    try:
        row = conn.execute(
            "SELECT status, output, token_in, token_out, end_time, duration_ms, name "
            "FROM spans WHERE id=?",
            (span.id,),
        ).fetchone()
        status, output, tin, tout, end, dur, name = row
        assert status == "success"
        assert output == '"答案"'
        assert (tin, tout) == (10, 20)
        assert end is not None and dur is not None
        assert name == "ask"  # 标识字段不被更新覆盖
    finally:
        conn.close()
        s.close()


def test_trace_upsert_updates_terminal_fields(tmp_path: object) -> None:
    s = Storage(tmp_path / "trace.db")
    trace = Trace(name="tutor")
    s.write_batch([trace.to_row()], [])
    trace.finish("failed", error="boom", total_tokens=99)
    s.write_batch([trace.to_row()], [])

    conn = connect_raw(s.db_path)
    try:
        status, end, err, tokens = conn.execute(
            "SELECT status, end_time, error, total_tokens FROM traces WHERE id=?",
            (trace.id,),
        ).fetchone()
        assert status == "failed" and err == "boom" and tokens == 99
        assert end is not None
    finally:
        conn.close()
        s.close()


def test_batch_is_atomic_all_or_nothing(tmp_path: object) -> None:
    s = Storage(tmp_path / "trace.db")
    traces = [Trace(name=f"t{i}").to_row() for i in range(10)]
    spans = [
        Span(trace_id=tr["id"], parent_id=None, type="step", name="s").to_row() for tr in traces
    ]
    s.write_batch(traces, spans)
    conn = connect_raw(s.db_path)
    try:
        assert conn.execute("SELECT count(*) FROM traces").fetchone()[0] == 10
        assert conn.execute("SELECT count(*) FROM spans").fetchone()[0] == 10
    finally:
        conn.close()
        s.close()


def test_span_parent_tree_roundtrip(tmp_path: object) -> None:
    s = Storage(tmp_path / "trace.db")
    root = Span(trace_id="t", parent_id=None, type="tool", name="root")
    child = Span(trace_id="t", parent_id=root.id, type="llm", name="child")
    s.write_batch([], [root.to_row(), child.to_row()])
    conn = connect_raw(s.db_path)
    try:
        parent_id = conn.execute("SELECT parent_id FROM spans WHERE id=?", (child.id,)).fetchone()
        assert parent_id[0] == root.id
    finally:
        conn.close()
        s.close()


def test_invalid_span_rolls_back_trace_in_same_transaction(tmp_path):
    db = tmp_path / "rollback.db"
    storage = Storage(db)
    trace = Trace(name="rollback")
    invalid = Span(trace_id=trace.id, parent_id=None, type="step", name="invalid").to_row()
    invalid["name"] = None
    try:
        with pytest.raises(sqlite3.IntegrityError):
            storage.write_batch([trace.to_row()], [invalid])
        with sqlite3.connect(db) as conn:
            assert conn.execute("SELECT COUNT(*) FROM traces").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM spans").fetchone()[0] == 0
    finally:
        storage.close()


def test_future_schema_is_rejected_without_changing_database(tmp_path):
    db = tmp_path / "future.db"
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA user_version=999")
    conn.close()
    with pytest.raises(sqlite3.DatabaseError, match="schema"):
        Storage(db)
    # 失败的构造函数必须关掉连接，Windows 下文件也能立即移动。
    renamed = db.with_name("future-renamed.db")
    db.rename(renamed)
    with sqlite3.connect(renamed) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 999


def test_running_trace_tokens_reflect_persisted_spans_without_double_counting(tmp_path):
    db = tmp_path / "running.db"
    storage = Storage(db)
    trace = Trace(name="running")
    span = Span(trace_id=trace.id, parent_id=None, type="llm", name="llm")
    try:
        storage.write_batch([trace.to_row()], [span.to_row()])
        span.finish("success", token_in=10, token_out=20)
        storage.write_batch([], [span.to_row()])
        storage.write_batch([], [span.to_row()])
        with sqlite3.connect(db) as conn:
            assert conn.execute("SELECT status,total_tokens FROM traces").fetchone() == (
                "running", 30,
            )
    finally:
        storage.close()


def test_token_updates_handle_duplicates_decreases_and_trace_replacement(tmp_path):
    storage = Storage(tmp_path / "delta.db")
    trace = Trace(name="delta")
    first = Span(trace_id=trace.id, parent_id=None, type="llm", name="first")
    second = Span(trace_id=trace.id, parent_id=None, type="llm", name="second")
    try:
        storage.write_batch([trace.to_row()], [])
        first.finish("success", token_in=10, token_out=20)
        second.finish("success", token_in=3, token_out=4)
        storage.write_batch([], [first.to_row(), first.to_row(), second.to_row()])
        assert storage._conn.execute("SELECT total_tokens FROM traces").fetchone()[0] == 37
        first.finish("success", token_in=0, token_out=0)
        storage.write_batch([], [first.to_row(), first.to_row()])
        assert storage._conn.execute("SELECT total_tokens FROM traces").fetchone()[0] == 7
        trace.finish("success", total_tokens=999)  # 同批聚合覆盖后也应以实际 Span 为准。
        storage.write_batch([trace.to_row()], [second.to_row()])
        assert storage._conn.execute("SELECT total_tokens FROM traces").fetchone()[0] == 7
        invalid = second.to_row() | {"name": None}
        with pytest.raises(sqlite3.IntegrityError):
            storage.write_batch([], [first.to_row() | {"token_in": 100}, invalid])
        assert storage._conn.execute("SELECT total_tokens FROM traces").fetchone()[0] == 7
        assert storage._conn.execute(
            "SELECT token_in FROM spans WHERE id=?", (first.id,),
        ).fetchone()[0] == 0
    finally:
        storage.close()


def test_large_batch_and_retries_keep_exact_tokens_without_scanning_entire_tree(tmp_path):
    storage = Storage(tmp_path / "large.db")
    trace = Trace(name="large")
    spans = [Span(trace_id=trace.id, parent_id=None, type="llm", name=str(i), token_in=1)
             for i in range(1200)]
    try:
        storage.write_batch([trace.to_row()], [span.to_row() for span in spans])
        statements = []
        storage._conn.set_trace_callback(statements.append)
        spans[0].finish("success", token_in=2, token_out=1)
        storage.write_batch([], [spans[0].to_row()])
        storage.write_batch([], [spans[0].to_row()])
        assert storage._conn.execute("SELECT total_tokens FROM traces").fetchone()[0] == 1202
        assert not any("SUM(" in statement.upper() for statement in statements)
    finally:
        storage.close()
