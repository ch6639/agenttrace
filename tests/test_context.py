"""context 模块测试（Sprint 1 任务 2，TDD 先行）。

覆盖：FR-2.3 嵌套关系自动推导（push/pop 栈语义）、
asyncio task 继承上下文副本、跨线程不传播（已声明的限制）。
"""

import asyncio
import threading

from agenttrace import context
from agenttrace.context import SpanRef


def test_empty_by_default() -> None:
    assert context.current() is None
    assert context.current_trace_id() is None


def test_push_pop_restores_previous() -> None:
    outer = SpanRef(trace_id="t", span_id="s1")
    tok1 = context.push(outer)
    assert context.current() == outer

    inner = SpanRef(trace_id="t", span_id="s2")
    tok2 = context.push(inner)
    assert context.current() == inner

    context.pop(tok2)
    assert context.current() == outer
    context.pop(tok1)
    assert context.current() is None


def test_current_trace_id_follows_stack() -> None:
    token = context.push(SpanRef(trace_id="t9", span_id="s"))
    assert context.current_trace_id() == "t9"
    context.pop(token)


def test_asyncio_task_inherits_stack() -> None:
    async def child() -> context.SpanRef | None:
        return context.current()

    async def main() -> context.SpanRef | None:
        token = context.push(SpanRef(trace_id="t", span_id="s"))
        try:
            return await asyncio.create_task(child())
        finally:
            context.pop(token)

    assert asyncio.run(main()) == SpanRef(trace_id="t", span_id="s")


def test_fresh_thread_does_not_inherit() -> None:
    results: list[object] = []

    def worker() -> None:
        results.append(context.current())

    token = context.push(SpanRef(trace_id="t", span_id="s"))
    try:
        t = threading.Thread(target=worker)
        t.start()
        t.join()
    finally:
        context.pop(token)
    assert results == [None]  # 已知限制（AT-DES-001 §5.2）：ContextVar 不随线程传播
