"""函数与 LLM 上下文拦截。业务返回值、异常与协程语义保持不变。"""

from __future__ import annotations

import inspect
from functools import wraps
from typing import Any

from agenttrace import api, context
from agenttrace.model import (
    Span,
    Trace,
    _configured_secrets,
    _redaction_secrets,
    dumps,
    estimate_tokens,
    redact_text,
)

_UNSET = object()


class SpanHandle:
    """流式场景由调用方聚合 chunk，再 update 完整输出；退出时提交终态。"""

    def __init__(self, span: Span | None = None, *, capture_output: bool = True) -> None:
        self.span = span
        self.capture_output = capture_output
        self._token_in: int | None = None
        self._token_out: int | None = None
        self._output_text = ""
        self._error: str | None = None

    def update(
        self, *, output: Any = _UNSET, token_in: int | None = None,
        token_out: int | None = None, model: str | None = None,
        error: Any = None, status_message: str | None = None,
    ) -> None:
        if self.span is None:
            return
        try:
            if output is not _UNSET:
                encoded = dumps(output) if self.capture_output else None
                self._output_text = output if isinstance(output, str) else encoded or ""
                if self.capture_output:
                    self.span.output = encoded
            if token_in is not None:
                self._token_in = max(0, int(token_in))
            if token_out is not None:
                self._token_out = max(0, int(token_out))
            if model is not None:
                self.span.model = redact_text(model)
            if error is not None:
                self._error = redact_text(str(error))
            if status_message is not None:
                self.span.status_message = redact_text(status_message)
        except Exception:
            pass  # 观测参数/序列化失败不得影响业务


class Observation:
    def __init__(
        self, name: str, kind: str, *, root: bool = False, input: Any = None,
        model: str | None = None, is_stream: bool = False,
        capture_input: bool = True, capture_output: bool = True,
    ) -> None:
        self.name, self.kind, self.root = name, kind, root
        self.input, self.model, self.is_stream = input, model, is_stream
        self.capture_input, self.capture_output = capture_input, capture_output
        self.trace: Trace | None = None
        self.runtime: api.Runtime | None = None
        self.token = None
        self.secret_token = None
        self.handle = SpanHandle(capture_output=capture_output)

    def __enter__(self) -> SpanHandle:
        try:
            parent = context.current()
            if parent is None and not self.root:
                return self.handle
            self.runtime = api.get_runtime()
            if self.runtime is None:
                return self.handle
            if parent is None:
                self.secret_token = _redaction_secrets.set(_configured_secrets())
                self.trace = Trace(name=redact_text(self.name))
            span = Span(
                trace_id=parent.trace_id if parent else self.trace.id,
                parent_id=parent.span_id if parent else None,
                type=self.kind, name=redact_text(self.name),
                model=redact_text(self.model) if self.model else None,
                is_stream=self.is_stream,
                input=dumps(self.input) if self.capture_input else None,
            )
            self.handle.span = span
            if self.trace is not None:
                self.runtime.pipeline.enqueue_root(self.trace.to_row(), span.to_row())
            else:
                self.runtime.pipeline.enqueue_span(span.to_row())
            self.token = context.push(context.SpanRef(span.trace_id, span.id))
        except Exception:
            self.handle.span = None
        return self.handle

    def __exit__(self, exc_type, exc, tb) -> bool:
        try:
            span = self.handle.span
            if span is not None and self.runtime is not None:
                error = self.handle._error
                if exc_type is not None:
                    # 不捕获 traceback locals/请求头；只保存脱敏后的类型和消息。
                    error = redact_text(f"{exc_type.__name__}: {exc}")
                status = "failed" if exc_type is not None or error is not None else "success"
                message = error or span.status_message
                tin, tout = self.handle._token_in, self.handle._token_out
                if span.type == "llm":
                    if tin is None or tout is None:
                        message = (message + "; " if message else "") + "__estimated__: true"
                    tin = estimate_tokens(span.input or "") if tin is None else tin
                    tout = estimate_tokens(self.handle._output_text) if tout is None else tout
                span.finish(status, status_message=message, token_in=tin, token_out=tout)
                if self.trace is not None:
                    self.trace.finish(status, error=error)
                    self.runtime.pipeline.enqueue_root(self.trace.to_row(), span.to_row())
                else:
                    self.runtime.pipeline.enqueue_span(span.to_row())
        except Exception:
            pass
        finally:
            if self.token is not None:
                context.pop(self.token)
            if self.secret_token is not None:
                _redaction_secrets.reset(self.secret_token)
        return False


def session(name: str = "session", **kwargs) -> Observation:
    """一次任务的上下文管理器；嵌套 session 作为现有 trace 的子步骤。"""
    return Observation(name, "step", root=True, **kwargs)


def llm(
    *, model: str, name: str = "llm", input: Any = None, is_stream: bool = False,
    capture_input: bool = True, capture_output: bool = True,
) -> Observation:
    return Observation(
        name, "llm", model=model, input=input, is_stream=is_stream,
        capture_input=capture_input, capture_output=capture_output,
    )


def _decorate(name, kind, root, capture_input, capture_output):
    def decorate(func):
        # 生成器必须在迭代期间记录上下文；当前显式拒绝，避免记录虚假成功。
        if inspect.isgeneratorfunction(func) or inspect.isasyncgenfunction(func):
            raise TypeError("generator functions: use session()/llm() around iteration")
        try:
            signature = inspect.signature(func)
        except (TypeError, ValueError):
            signature = None

        def observation(args, kwargs):
            payload = None
            if capture_input:
                try:
                    payload = dict(signature.bind(*args, **kwargs).arguments)
                    payload.pop("self", None)
                    payload.pop("cls", None)
                except Exception:
                    payload = {"args": args, "kwargs": kwargs}
            return Observation(
                name or func.__name__, kind, root=root, input=payload,
                capture_input=capture_input, capture_output=capture_output,
            )

        if inspect.iscoroutinefunction(func):
            @wraps(func)
            async def async_wrapper(*args, **kwargs):
                with observation(args, kwargs) as handle:
                    result = await func(*args, **kwargs)
                    handle.update(output=result)
                    return result
            return async_wrapper

        @wraps(func)
        def wrapper(*args, **kwargs):
            with observation(args, kwargs) as handle:
                result = func(*args, **kwargs)
                handle.update(output=result)
                return result
        return wrapper
    return decorate


def trace(name: str | None = None, *, capture_input=True, capture_output=True):
    return _decorate(name, "step", True, capture_input, capture_output)


def tool(name: str | None = None, *, capture_input=True, capture_output=True):
    return _decorate(name, "tool", False, capture_input, capture_output)


def log_step(name: str, data: Any = None) -> None:
    try:
        ref = context.current()
        runtime = api.get_runtime() if ref else None
        if runtime is None or ref is None:
            return
        span = Span(
            trace_id=ref.trace_id, parent_id=ref.span_id,
            type="step", name=redact_text(name), input=dumps(data),
        )
        span.finish("success")
        span.duration_ms = None
        runtime.pipeline.enqueue_span(span.to_row())
    except Exception:
        pass
