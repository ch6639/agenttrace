"""pipeline 摄入管线测试（Sprint 1 任务 4，TDD 先行）。

覆盖（AT-DES-001 §5.1）：有界队列丢最旧、批量/时间/强制三种触发、
写失败重试与降级不抛异常（NFR-3）、flush 排空、shutdown 停线程、
span token 聚合回填 trace.total_tokens。
用可注入的假 Storage 断言行为，不依赖真实时钟精度（等待均带宽限期）。
"""

import sqlite3
import threading
import time

import pytest

from agenttrace.pipeline import Pipeline


class FakeStorage:
    """记录 write_batch 调用；前 failures 次抛错用于故障注入（TC-20 内核）。"""

    def __init__(self, failures: int = 0) -> None:
        self.calls = 0
        self.failures = failures
        self.trace_rows: list[dict] = []
        self.span_rows: list[dict] = []

    def write_batch(self, traces, spans) -> None:
        self.calls += 1
        if self.calls <= self.failures:
            raise sqlite3.OperationalError("simulated disk error")
        self.trace_rows.extend(traces)
        self.span_rows.extend(spans)


def make_row(i: int) -> dict:
    return {"id": f"row-{i}"}


def wait_until(cond, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


@pytest.fixture
def storage() -> FakeStorage:
    return FakeStorage()


def test_enqueue_bounded_drops_oldest(storage: FakeStorage) -> None:
    p = Pipeline(storage, capacity=8)  # 不 start 也可验证入队语义
    for i in range(13):
        p.enqueue_span(make_row(i))
    stats = p.stats
    assert stats["queued"] == 8
    assert stats["dropped"] == 5  # 满则丢弃最旧（AT-DES-001 §5.1）
    with p._cond:
        remaining = [row for _kind, row in p._queue]
    assert remaining[0]["id"] == "row-5"  # 最旧的 row-0~4 被丢弃


def test_flush_drains_all_rows(storage: FakeStorage) -> None:
    p = Pipeline(storage, batch_size=32, flush_interval=999).start()
    try:
        for i in range(5):
            p.enqueue_span(make_row(i))
        assert p.flush(timeout=5) is True
        assert p.stats["queued"] == 0
        assert len(storage.span_rows) == 5
    finally:
        p.shutdown()


def test_batch_size_triggers_background_write(storage: FakeStorage) -> None:
    p = Pipeline(storage, batch_size=4, flush_interval=999).start()
    try:
        for i in range(4):
            p.enqueue_span(make_row(i))
        assert wait_until(lambda: len(storage.span_rows) == 4)  # 未调用 flush
    finally:
        p.shutdown()


def test_time_interval_triggers_background_write(storage: FakeStorage) -> None:
    p = Pipeline(storage, batch_size=999, flush_interval=0.05).start()
    try:
        for i in range(2):
            p.enqueue_span(make_row(i))
        assert wait_until(lambda: len(storage.span_rows) == 2)
    finally:
        p.shutdown()


def test_permanent_failure_degrades_without_raising() -> None:
    storage = FakeStorage(failures=999)
    p = Pipeline(storage, batch_size=999, flush_interval=999, max_retries=2).start()
    try:
        for i in range(3):
            p.enqueue_span(make_row(i))
        assert p.flush(timeout=10) is True  # 队列已清空（该批被跳过）
        assert storage.calls == 3  # 首次 + 2 次重试
        assert storage.span_rows == []  # 数据丢弃，但绝不抛入业务线程（NFR-3）
    finally:
        p.shutdown()


def test_transient_failure_recovers() -> None:
    storage = FakeStorage(failures=2)
    p = Pipeline(storage, batch_size=999, flush_interval=999, max_retries=3).start()
    try:
        for i in range(3):
            p.enqueue_span(make_row(i))
        assert p.flush(timeout=10) is True
        assert storage.calls == 3
        assert len(storage.span_rows) == 3  # 重试成功，无丢失
    finally:
        p.shutdown()


def test_shutdown_stops_flusher_thread(storage: FakeStorage) -> None:
    p = Pipeline(storage).start()
    assert p._thread is not None
    thread: threading.Thread = p._thread
    assert thread.is_alive()
    p.shutdown(timeout=5)
    assert not thread.is_alive()


def test_total_tokens_aggregated_into_trace(storage: FakeStorage) -> None:
    p = Pipeline(storage, batch_size=999, flush_interval=999).start()
    try:
        p.enqueue_span({"id": "s1", "trace_id": "t1", "token_in": 10, "token_out": 20})
        p.enqueue_span({"id": "s2", "trace_id": "t1", "token_in": 5, "token_out": 5})
        p.enqueue_trace({"id": "t1", "status": "success", "total_tokens": 0})
        assert p.flush(timeout=5) is True
        assert storage.trace_rows[0]["total_tokens"] == 40
    finally:
        p.shutdown()


def test_flush_waits_for_inflight_transaction():
    entered, release = threading.Event(), threading.Event()

    class BlockingStorage(FakeStorage):
        def write_batch(self, traces, spans):
            entered.set()
            assert release.wait(3)
            super().write_batch(traces, spans)

    storage = BlockingStorage()
    p = Pipeline(storage, batch_size=1).start()
    try:
        p.enqueue_span(make_row(1))
        assert entered.wait(2)
        assert p.flush(timeout=0.02) is False
        release.set()
        assert p.flush(timeout=2) is True
        assert len(storage.span_rows) == 1
    finally:
        release.set()
        p.shutdown()


def test_span_updates_do_not_double_count_tokens(storage):
    p = Pipeline(storage, flush_interval=999).start()
    try:
        row = {"id": "s", "trace_id": "t", "token_in": 10, "token_out": 5}
        p.enqueue_span(row)
        assert p.flush()
        p.enqueue_span({**row, "token_out": 9})
        p.enqueue_trace({"id": "t", "status": "success", "total_tokens": 0})
        assert p.flush()
        assert storage.trace_rows[-1]["total_tokens"] == 19
        assert not p._tokens  # 终态后释放每条 trace 的聚合内存
    finally:
        p.shutdown()


def test_async_overload_is_counted_and_warns_without_leaking_data(capsys):
    entered, release = threading.Event(), threading.Event()

    class SlowStorage(FakeStorage):
        def write_batch(self, traces, spans):
            entered.set()
            assert release.wait(3)
            super().write_batch(traces, spans)

    storage = SlowStorage()
    p = Pipeline(storage, capacity=4, batch_size=1).start()
    try:
        p.enqueue_span(make_row(0))
        assert entered.wait(2)
        for i in range(1, 101):
            p.enqueue_span({**make_row(i), "private": "sensitive-payload"})
        assert p.stats["queued"] == 4
        assert p.stats["dropped"] == 96
        release.set()
        assert p.flush()
        assert p.stats["written_events"] == 5
        warning = capsys.readouterr().err
        assert "丢弃" in warning and "sensitive-payload" not in warning
        assert len(warning.splitlines()) <= 8
    finally:
        release.set()
        p.shutdown()


def test_bad_event_does_not_kill_async_writer(storage):
    p = Pipeline(storage).start()
    try:
        p.enqueue_span({"id": "bad", "trace_id": "t", "token_in": "not-a-number"})
        assert p.flush()
        assert p.stats["failed_batches"] == 1
        p.enqueue_span(make_row(1))
        assert p.flush()
        assert storage.span_rows == [make_row(1)]
        assert p._thread.is_alive()
    finally:
        p.shutdown()


def test_durable_flush_and_shutdown_time_out_without_closing_inflight_write():
    entered, release = threading.Event(), threading.Event()

    class BlockingStorage(FakeStorage):
        def write_batch(self, traces, spans):
            entered.set()
            assert release.wait(3)
            super().write_batch(traces, spans)

    storage = BlockingStorage()
    p = Pipeline(storage, durability="durable").start()
    worker = threading.Thread(target=p.enqueue_span, args=(make_row(1),))
    worker.start()
    try:
        assert entered.wait(2)
        assert p.flush(timeout=0.01) is False
        assert p.shutdown(timeout=0.01) is False
        release.set()
        worker.join(timeout=2)
        assert not worker.is_alive()
        assert p.shutdown() is True
        assert storage.span_rows == [make_row(1)]
        p.enqueue_span(make_row(2))
        assert p.stats["dropped"] == 1
    finally:
        release.set()
        worker.join(timeout=2)
        p.shutdown()
