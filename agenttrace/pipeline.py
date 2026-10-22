"""两种摄入模式：durable 逐事件确认提交；async 有界队列批量写入。

SDK 默认 durable；Pipeline 的默认 async 保留底层批处理调用兼容性。
两者均有有限重试、失败统计、flush/shutdown 和 Token 增量聚合。
"""

from __future__ import annotations

import sys
import threading
import time
from collections import deque
from collections.abc import Mapping
from typing import Any, Protocol


class WriteStorage(Protocol):
    """管线依赖的存储接口（storage.Storage 即其实现；测试注入假实现）。"""

    def write_batch(self, traces: list, spans: list) -> None: ...


_TERMINAL_STATUS = ("success", "failed")


class Pipeline:
    """durable 串行提交确认，或 async 入队后由 flusher 单线程批量落盘。"""

    def __init__(
        self,
        storage: WriteStorage,
        *,
        capacity: int = 1024,
        batch_size: int = 32,
        flush_interval: float = 0.5,
        max_retries: int = 3,
        durability: str = "async",
    ) -> None:
        if capacity < 1 or batch_size < 1 or flush_interval <= 0 or max_retries < 0:
            raise ValueError("invalid pipeline limits")
        if durability not in ("durable", "async"):
            raise ValueError("durability must be 'durable' or 'async'")
        self.durability = durability
        self._storage = storage
        self._capacity = capacity
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._max_retries = max_retries
        self._queue: deque[tuple[str, dict[str, Any]]] = deque()
        self._cond = threading.Condition()
        self._write_lock = threading.Lock()
        self._dropped = 0
        self._force_flush = False
        self._stop = False
        self._writing = False
        self._failed_batches = 0
        self._written_events = 0
        self._tokens: dict[str, int] = {}  # trace_id -> 累计 token
        self._span_tokens: dict[str, dict[str, int]] = {}
        self._thread: threading.Thread | None = None

    # —— 生命周期 ——————————————————————————————————————————————

    def start(self) -> Pipeline:
        if self.durability == "durable":
            return self
        if self._thread is None:
            self._thread = threading.Thread(
                target=self._run, name="agenttrace-flusher", daemon=True
            )
            self._thread.start()
        return self

    def shutdown(self, timeout: float = 5.0) -> bool:
        """排空队列后停止 flusher 线程（api.atexit 兜底调用）。"""
        with self._cond:
            self._stop = True
            self._force_flush = True
            self._cond.notify_all()
            if self.durability == "durable":
                deadline = time.monotonic() + timeout
                while self._writing:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return False
                    self._cond.wait(remaining)
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        return self._thread is None or not self._thread.is_alive()

    def flush(self, timeout: float = 5.0) -> bool:
        """阻塞至队列排空；超时返回 False（数据仍在队列，进程未退出不丢失）。"""
        deadline = time.monotonic() + timeout
        with self._cond:
            self._force_flush = True
            self._cond.notify_all()
            while self._queue or self._writing:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._cond.wait(remaining)
            return True

    # —— 业务线程侧：入队即返回 ——————————————————————————————————

    def enqueue_trace(self, row: Mapping[str, Any]) -> None:
        self._enqueue("trace", row)

    def enqueue_span(self, row: Mapping[str, Any]) -> None:
        self._enqueue("span", row)

    def enqueue_root(self, trace: Mapping[str, Any], span: Mapping[str, Any]) -> None:
        """根事件的 Trace 聚合与根 Span 同一事务，避免双次 fsync 和半个根事件。"""
        if self.durability == "durable":
            self._commit_events([("trace", dict(trace)), ("span", dict(span))])
        else:
            self.enqueue_trace(trace)
            self.enqueue_span(span)

    @property
    def stats(self) -> dict[str, int]:
        with self._cond:
            return {
                "queued": len(self._queue), "dropped": self._dropped,
                "failed_batches": self._failed_batches,
                "written_events": self._written_events,
            }

    # —— 内部实现 ——————————————————————————————————————————————

    def _enqueue(self, kind: str, row: Mapping[str, Any]) -> None:
        if self.durability == "durable":
            self._commit_events([(kind, dict(row))])
            return
        with self._cond:
            if self._stop:
                self._record_drop()
                return
            if len(self._queue) >= self._capacity:
                self._queue.popleft()  # 满则丢弃最旧 + 计数（AT-DES-001 §5.1）
                self._record_drop()
            self._queue.append((kind, dict(row)))
            if len(self._queue) == 1 or len(self._queue) >= self._batch_size:
                self._cond.notify()  # 攒满一批，提前唤醒 flusher

    def _commit_events(self, batch: list[tuple[str, dict[str, Any]]]) -> None:
        # 单个写入事务串行化；返回时已经提交，不依赖队列或 atexit。
        with self._write_lock:
            with self._cond:
                if self._stop:
                    for _ in batch:
                        self._record_drop()
                    return
                self._writing = True
            try:
                self._write_batch(batch)
            finally:
                with self._cond:
                    self._writing = False
                    self._cond.notify_all()

    def _record_drop(self) -> None:
        self._dropped += 1
        # 指数节流：过载时告警可见，又不逐条刷屏；不输出事件内容。
        if self._dropped & (self._dropped - 1) == 0:
            print(f"[agenttrace] 已丢弃 {self._dropped} 条观测事件，请检查 diagnostics()",
                  file=sys.stderr)

    def _run(self) -> None:
        last_write = time.monotonic()
        while True:
            with self._cond:
                # 无事件时阻塞，避免超过 flush_interval 后持续空转占用 CPU。
                while not self._queue and not self._stop:
                    self._cond.wait()
                if not self._queue and self._stop:
                    return
                if not (self._force_flush or self._stop or len(self._queue) >= self._batch_size):
                    idle = self._flush_interval - (time.monotonic() - last_write)
                    self._cond.wait(timeout=max(0.0, idle))
                if not self._queue and self._stop:
                    return
                due = (
                    self._force_flush
                    or self._stop
                    or len(self._queue) >= self._batch_size
                    or time.monotonic() - last_write >= self._flush_interval
                )
                if not (due and self._queue):
                    continue
                batch = list(self._queue)
                self._queue.clear()
                self._force_flush = False
                self._writing = True
            try:
                self._write_batch(batch)  # 持锁外写库，不阻塞入队
            finally:
                last_write = time.monotonic()
                with self._cond:
                    self._writing = False
                    self._cond.notify_all()  # 唤醒 flush() 重新检查队列

    def _write_batch(self, batch: list[tuple[str, dict[str, Any]]]) -> None:
        traces: list[dict[str, Any]] = []
        spans: list[dict[str, Any]] = []
        # 同批先处理 span，再回填 trace，避免根终态排在子事件之前时遗漏 token。
        try:
            for kind, row in sorted(batch, key=lambda event: event[0] == "trace"):
                row = self._prepare(kind, row)
                (traces if kind == "trace" else spans).append(row)
        except Exception:
            self._record_failure(len(batch))
            return
        for attempt in range(self._max_retries + 1):
            try:
                self._storage.write_batch(traces, spans)
                with self._cond:
                    self._written_events += len(batch)
                return
            except Exception:
                if attempt >= self._max_retries:
                    self._record_failure(len(batch))
                    return
                time.sleep(0.02 * (attempt + 1))  # 简单退避后重试

    def _record_failure(self, count: int) -> None:
        with self._cond:
            self._failed_batches += 1
        print(f"[agenttrace] 存储写入失败，跳过本批 {count} 条事件"
              "（旁路失效，不影响被测程序，NFR-3）", file=sys.stderr)

    def _prepare(self, kind: str, row: Mapping[str, Any]) -> dict[str, Any]:
        """token 聚合：span 累加到所属 trace；trace 终态回填 total_tokens。"""
        out = dict(row)
        if kind == "span":
            trace_id = out.get("trace_id")
            if trace_id:
                tokens = int(out.get("token_in") or 0) + int(out.get("token_out") or 0)
                by_span = self._span_tokens.setdefault(trace_id, {})
                previous = by_span.get(out["id"], 0)
                by_span[out["id"]] = tokens
                self._tokens[trace_id] = self._tokens.get(trace_id, 0) + tokens - previous
        elif out.get("status") in _TERMINAL_STATUS:
            trace_id = out.get("id", "")
            if not out.get("total_tokens"):
                out["total_tokens"] = self._tokens.get(trace_id, 0)
            self._tokens.pop(trace_id, None)
            self._span_tokens.pop(trace_id, None)
        return out
