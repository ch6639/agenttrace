"""摄入管线（agenttrace/pipeline，FR-2.4/NFR-1/NFR-3）。

职责（AT-DES-001 §5.1，时序图 models/sequence-ingestion.puml）：
- 有界内存队列（容量 1024）：满则丢弃最旧 + 计数（旁路失效）；
- flusher 守护线程：32 条或 500ms 触发批量单事务 UPSERT；
- 写失败重试 ≤3 次 → 跳过该批 + stderr 告警，绝不抛入业务线程（NFR-3）；
- flush(timeout)/shutdown()；业务线程只"入队即返回"；
- span 的 token 用量在管线侧聚合，trace 终态落盘时回填 total_tokens
  （intercept 无需自行累计，保持拦截层简单）。

Sprint 1 任务 4（TDD：tests/test_pipeline.py 先行）。
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
    """异步批量摄入管线：入队即返回，落盘由 flusher 单线程完成。"""

    def __init__(
        self,
        storage: WriteStorage,
        *,
        capacity: int = 1024,
        batch_size: int = 32,
        flush_interval: float = 0.5,
        max_retries: int = 3,
    ) -> None:
        self._storage = storage
        self._capacity = capacity
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._max_retries = max_retries
        self._queue: deque[tuple[str, dict[str, Any]]] = deque()
        self._cond = threading.Condition()
        self._dropped = 0
        self._force_flush = False
        self._stop = False
        self._tokens: dict[str, int] = {}  # trace_id -> 累计 token
        self._thread: threading.Thread | None = None

    # —— 生命周期 ——————————————————————————————————————————————

    def start(self) -> Pipeline:
        if self._thread is None:
            self._thread = threading.Thread(
                target=self._run, name="agenttrace-flusher", daemon=True
            )
            self._thread.start()
        return self

    def shutdown(self, timeout: float = 5.0) -> None:
        """排空队列后停止 flusher 线程（api.atexit 兜底调用）。"""
        with self._cond:
            self._stop = True
            self._force_flush = True
            self._cond.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def flush(self, timeout: float = 5.0) -> bool:
        """阻塞至队列排空；超时返回 False（数据仍在队列，进程未退出不丢失）。"""
        deadline = time.monotonic() + timeout
        with self._cond:
            self._force_flush = True
            self._cond.notify_all()
            while self._queue:
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

    @property
    def stats(self) -> dict[str, int]:
        with self._cond:
            return {"queued": len(self._queue), "dropped": self._dropped}

    # —— 内部实现 ——————————————————————————————————————————————

    def _enqueue(self, kind: str, row: Mapping[str, Any]) -> None:
        with self._cond:
            if len(self._queue) >= self._capacity:
                self._queue.popleft()  # 满则丢弃最旧 + 计数（AT-DES-001 §5.1）
                self._dropped += 1
            self._queue.append((kind, dict(row)))
            if len(self._queue) >= self._batch_size:
                self._cond.notify()  # 攒满一批，提前唤醒 flusher

    def _run(self) -> None:
        last_write = time.monotonic()
        while True:
            with self._cond:
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
            self._write_batch(batch)  # 持锁外写库，不阻塞入队
            last_write = time.monotonic()
            with self._cond:
                self._cond.notify_all()  # 唤醒 flush() 重新检查队列

    def _write_batch(self, batch: list[tuple[str, dict[str, Any]]]) -> None:
        traces: list[dict[str, Any]] = []
        spans: list[dict[str, Any]] = []
        for kind, row in batch:
            row = self._prepare(kind, row)
            (traces if kind == "trace" else spans).append(row)
        for attempt in range(self._max_retries + 1):
            try:
                self._storage.write_batch(traces, spans)
                return
            except Exception:
                if attempt >= self._max_retries:
                    print(
                        f"[agenttrace] 存储写入失败，跳过本批 {len(batch)} 条事件"
                        "（旁路失效，不影响被测程序，NFR-3）",
                        file=sys.stderr,
                    )
                    return
                time.sleep(0.02 * (attempt + 1))  # 简单退避后重试

    def _prepare(self, kind: str, row: Mapping[str, Any]) -> dict[str, Any]:
        """token 聚合：span 累加到所属 trace；trace 终态回填 total_tokens。"""
        out = dict(row)
        if kind == "span":
            trace_id = out.get("trace_id")
            if trace_id:
                tokens = int(out.get("token_in") or 0) + int(out.get("token_out") or 0)
                self._tokens[trace_id] = self._tokens.get(trace_id, 0) + tokens
        elif out.get("status") in _TERMINAL_STATUS and not out.get("total_tokens"):
            out["total_tokens"] = self._tokens.get(out.get("id", ""), 0)
        return out
