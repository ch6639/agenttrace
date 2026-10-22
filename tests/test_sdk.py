"""SDK 集成验收：TC-04/05/06/08/20/23，真实 SQLite 落盘。"""

import asyncio
import json
import sqlite3

import pytest

import agenttrace as at
from agenttrace import context


@pytest.fixture
def database(tmp_path):
    db = tmp_path / "sdk.db"
    at.init(db_path=db)
    yield db
    at.shutdown()


def read(db, table):
    assert at.flush()
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]


def test_nested_tools_llm_and_step(database):
    @at.tool()
    def answer(question):
        with at.llm(model="mock", input=question, is_stream=True) as gen:
            gen.update(output="软件工程", token_in=12, token_out=6)
        return "软件工程"

    @at.trace()
    def tutor(question):
        at.log_step("route", {"next": "answer"})
        return answer(question)

    assert tutor("是什么？") == "软件工程"
    traces = read(database, "traces")
    spans = read(database, "spans")
    assert len(traces) == 1
    assert traces[0]["status"] == "success"
    assert traces[0]["total_tokens"] == 18
    root = next(s for s in spans if s["name"] == "tutor")
    tool = next(s for s in spans if s["type"] == "tool")
    llm = next(s for s in spans if s["type"] == "llm")
    assert root["parent_id"] is None
    assert tool["parent_id"] == root["id"]
    assert llm["parent_id"] == tool["id"]
    assert json.loads(tool["input"]) == {"question": "是什么？"}
    assert json.loads(tool["output"]) == "软件工程"
    assert llm["is_stream"] == 1 and llm["duration_ms"] is not None
    assert context.current() is None


def test_exception_identity_and_partial_stream_preserved(database):
    error = ValueError("stream interrupted")

    @at.trace()
    def task():
        with at.llm(model="mock", is_stream=True) as gen:
            gen.update(output="partial", token_in=2, token_out=1)
            raise error

    with pytest.raises(ValueError) as caught:
        task()
    assert caught.value is error
    assert context.current() is None
    assert read(database, "traces")[0]["status"] == "failed"
    span = next(s for s in read(database, "spans") if s["type"] == "llm")
    assert json.loads(span["output"]) == "partial"
    assert span["status"] == "failed" and "ValueError" in span["status_message"]


def test_session_nested_trace_and_outside_noop(database):
    @at.trace()
    def nested():
        return 3

    at.log_step("outside")
    with at.llm(model="outside") as gen:
        gen.update(output="not recorded")
    with at.session("outer"):
        assert nested() == 3
    assert len(read(database, "traces")) == 1
    assert len(read(database, "spans")) == 2


def test_async_tasks_keep_distinct_parents_and_cancellation(database):
    @at.tool()
    async def worker(i):
        await asyncio.sleep(0)
        with at.llm(model="mock") as gen:
            gen.update(output=str(i), token_in=1, token_out=2)
        return i

    @at.trace()
    async def task():
        return await asyncio.gather(worker(1), worker(2))

    assert asyncio.run(task()) == [1, 2]
    spans = read(database, "spans")
    tool_ids = {s["id"] for s in spans if s["type"] == "tool"}
    assert {s["parent_id"] for s in spans if s["type"] == "llm"} == tool_ids
    assert read(database, "traces")[0]["total_tokens"] == 6

    @at.trace()
    async def cancelled():
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(cancelled())
    assert read(database, "traces")[-1]["status"] == "failed"


def test_capture_switches_and_secrets(database, monkeypatch):
    secret = "sk-private-1234567890"
    monkeypatch.setenv("OPENAI_API_KEY", secret)

    @at.tool()
    def invoke(api_key, data):
        return {"authorization": "Bearer " + api_key, "answer": data}

    with at.session("safe"):
        invoke(secret, {"nested": {"access_token": secret}, "text": secret})
        with at.llm(model=secret, capture_output=False) as gen:
            gen.update(output=secret, token_in=2, token_out=3)
    rows = read(database, "spans")
    assert secret not in json.dumps(rows)
    llm = next(s for s in rows if s["type"] == "llm")
    assert llm["output"] is None and llm["token_out"] == 3


def test_disabled_and_storage_initialization_failure_do_not_break_business(tmp_path):
    @at.trace()
    def task(value):
        return value + 1

    at.init(db_path=tmp_path / "disabled.db", enabled=False)
    assert task(4) == 5
    assert not (tmp_path / "disabled.db").exists()
    at.init(db_path=tmp_path / "missing" / "unwritable.db")
    assert task(7) == 8
    at.shutdown()


def test_repr_failure_does_not_change_return_value(database):
    class Unrepresentable:
        def __repr__(self):
            raise RuntimeError("cannot repr")

    value = Unrepresentable()

    @at.trace()
    def task(arg):
        return arg

    assert task(value) is value
    assert read(database, "traces")[0]["status"] == "success"


def test_estimated_tokens_are_labelled(database):
    with at.session("estimate"):
        with at.llm(model="unknown", input="abcd") as gen:
            gen.update(output="abcdefgh")
    span = next(s for s in read(database, "spans") if s["type"] == "llm")
    assert span["token_in"] > 0 and span["token_out"] == 2
    assert "__estimated__" in span["status_message"]
