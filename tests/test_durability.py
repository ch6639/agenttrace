"""TC-07：真正终止子进程，验证已确认事件不依赖内存队列或 atexit。"""

import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path

import pytest

import agenttrace as at
from agenttrace.query import QueryStore


@pytest.mark.parametrize("phase", ["before", "during", "after"])
def test_force_kill_retains_all_acknowledged_events(tmp_path, phase):
    db, marker = tmp_path / "killed.db", tmp_path / "ready"
    helper = Path(__file__).parent / "helpers" / "crash_writer.py"
    process = subprocess.Popen(
        [sys.executable, str(helper), str(db), str(marker), phase],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 15
        while not marker.exists() and time.monotonic() < deadline and process.poll() is None:
            time.sleep(0.01)
        assert marker.exists(), "子进程未到达强杀测试点"
    finally:
        if process.poll() is None:
            process.kill()  # SIGKILL / Windows TerminateProcess：不执行 finally/atexit。
        _, error = process.communicate(timeout=5)
    assert not error, error.decode(errors="replace")
    # 不启动 SDK、不执行恢复写入，另一只读连接直接读取崩溃后数据。
    query = QueryStore(db)
    trace = query.list_traces()["items"][0]
    detail = query.get_trace(trace["id"])
    names = {span["name"] for span in detail["spans"]}
    assert {f"acknowledged-{i}" for i in range(20)} <= names
    assert ("in-flight" in names) == (phase == "after")
    assert detail["trace"]["status"] == "running"  # 不伪造任务终态。
    assert detail["trace"]["total_tokens"] == 3
    llm = next(span for span in detail["spans"] if span["type"] == "llm")
    assert llm["status"] == "success" and llm["output"] == "answer"
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


@pytest.mark.parametrize("durability", ["durable", "async"])
def test_event_becomes_visible_within_one_second_without_flush(tmp_path, durability):
    db = tmp_path / "visible.db"
    at.init(db, durability=durability)
    try:
        with at.session("visibility"):
            start = time.monotonic()
            at.log_step("visible")
            with closing(sqlite3.connect(db)) as conn:
                while time.monotonic() - start < 1:
                    row = conn.execute("SELECT COUNT(*) FROM spans WHERE name='visible'").fetchone()
                    if row[0]:
                        break
                    time.sleep(0.005)
                else:
                    pytest.fail("事件在产生后 1 秒内未对独立连接可见")
                assert time.monotonic() - start < 1
    finally:
        at.shutdown()


def test_default_uses_full_sqlite_durability(tmp_path):
    from agenttrace import api
    at.init(tmp_path / "full.db")
    try:
        with at.session("default"):
            runtime = api.get_runtime()
            assert runtime.storage._conn.execute("PRAGMA synchronous").fetchone()[0] == 2
            assert runtime.pipeline.stats["queued"] == 0
            assert at.diagnostics()["durability"] == "durable"
            assert at.diagnostics()["failed_batches"] == 0
    finally:
        at.shutdown()
