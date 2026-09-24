"""上下文栈（agenttrace/context，FR-2.3）。

职责（AT-DES-001 §5.2）：
- contextvars.ContextVar 维护"当前 span 栈"，@trace/@tool/with llm() 进入即 push、
  退出即 pop，parent_id 自动取栈顶，开发者零感知；
- asyncio task 继承上下文副本，协程内嵌套关系正确；
- 已知限制：跨线程（线程池）不传播，本期非目标（demo Agent 不使用线程池）。

Sprint 1 任务 2（TDD：tests/test_context.py 先行）。
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass


@dataclass(frozen=True)
class SpanRef:
    """栈帧引用：当前所处 span 及其所属 trace。"""

    trace_id: str
    span_id: str


# 不可变元组栈：push 时整体替换（拷贝），asyncio task 复制的上下文因此互不干扰
_stack: ContextVar[tuple[SpanRef, ...]] = ContextVar("agenttrace_stack", default=())


def current() -> SpanRef | None:
    """栈顶即当前 span；空栈（无活动 trace）返回 None。"""
    stack = _stack.get()
    return stack[-1] if stack else None


def current_trace_id() -> str | None:
    ref = current()
    return ref.trace_id if ref else None


def push(ref: SpanRef) -> Token:
    """进入一个 span：返回 token，退出时必须 pop(token) 恢复上层。"""
    return _stack.set(_stack.get() + (ref,))


def pop(token: Token) -> None:
    """退出 span：恢复 push 之前的栈（异常路径同样必须调用）。"""
    _stack.reset(token)
