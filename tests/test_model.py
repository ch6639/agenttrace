"""model 模块测试（Sprint 1 任务 1，TDD 先行）。

覆盖：FR-2.1/2.2（事件字段完整）、D-08（64KiB 截断与 token 近似计数）、
字段集与 AT-DES-001 §3.2 DDL 严格一致、finish 终态计算。
"""

import json

from agenttrace import model
from agenttrace.model import MAX_FIELD_BYTES, Span, Trace, dumps, estimate_tokens

TRACE_COLUMNS = {
    "id", "name", "session_id", "status", "start_time", "end_time",
    "total_tokens", "latency_ms", "error", "created_at",
}

SPAN_COLUMNS = {
    "id", "trace_id", "parent_id", "type", "name", "input", "output",
    "status", "status_message", "model", "token_in", "token_out",
    "start_time", "end_time", "duration_ms", "is_stream", "created_at",
}


def test_span_to_row_matches_ddl_columns() -> None:
    span = Span(trace_id="t1", parent_id=None, type=model.TYPE_STEP, name="decide")
    assert set(span.to_row()) == SPAN_COLUMNS


def test_trace_to_row_matches_ddl_columns() -> None:
    assert set(Trace(name="tutor").to_row()) == TRACE_COLUMNS


def test_defaults_and_unique_ids() -> None:
    a, b = Trace(name="a"), Trace(name="b")
    assert a.id != b.id
    assert a.status == model.STATUS_RUNNING
    assert a.start_time and a.created_at
    span = Span(trace_id=a.id, parent_id=None, type=model.TYPE_LLM, name="llm")
    assert span.token_in == 0 and span.token_out == 0
    assert span.is_stream is False


def test_dumps_dict_roundtrip() -> None:
    payload = {"messages": [{"role": "user", "content": "设问式导入是什么"}]}
    assert json.loads(dumps(payload)) == payload


def test_dumps_non_serializable_falls_back_to_repr() -> None:
    text = dumps({"obj": object()})
    assert "object at" in text


def test_dumps_truncates_oversized_value() -> None:
    wrapped = json.loads(dumps("x" * (MAX_FIELD_BYTES * 4)))
    assert wrapped["__truncated__"] is True
    assert wrapped["head"]
    assert len(wrapped["head"].encode("utf-8")) <= MAX_FIELD_BYTES


def test_estimate_tokens_is_len_div_4() -> None:
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abc") == 0
    assert estimate_tokens("") == 0


def test_span_finish_sets_end_duration_and_fields() -> None:
    span = Span(trace_id="t", parent_id=None, type=model.TYPE_TOOL, name="search")
    span.finish(
        model.STATUS_FAILED,
        output=dumps({"err": 1}),
        status_message="KeyError('kw')",
        token_in=3,
        token_out=5,
    )
    assert span.status == model.STATUS_FAILED
    assert span.output == '{"err": 1}'
    assert span.status_message == "KeyError('kw')"
    assert span.token_in == 3 and span.token_out == 5
    assert span.end_time is not None
    assert span.duration_ms is not None and span.duration_ms >= 0


def test_trace_finish_sets_latency_and_tokens() -> None:
    trace = Trace(name="tutor")
    trace.finish(model.STATUS_SUCCESS, total_tokens=128)
    assert trace.status == model.STATUS_SUCCESS
    assert trace.total_tokens == 128
    assert trace.end_time is not None
    assert trace.latency_ms is not None and trace.latency_ms >= 0


def test_type_and_status_constants() -> None:
    assert {model.TYPE_LLM, model.TYPE_TOOL, model.TYPE_STEP} == {"llm", "tool", "step"}
    assert {model.STATUS_RUNNING, model.STATUS_SUCCESS, model.STATUS_FAILED} == {
        "running", "success", "failed",
    }
