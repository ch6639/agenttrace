"""TC-02：同一份业务源码，接入与未接入的行为、调用顺序及异常相同。"""

import ast
import inspect

import pytest

import agenttrace as at
from agenttrace.query import QueryStore
from examples.demo_agent import business, main


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("fail_notes", [False, True])
def test_business_source_and_behavior_are_unchanged(tmp_path, monkeypatch, stream, fail_notes):
    db = tmp_path / "noninvasive.db"
    calls = []
    original_call = main.call_llm

    def spy(prompt, **kwargs):
        calls.append((prompt, kwargs["model"], kwargs["stream"]))
        yield from original_call(prompt, **kwargs)

    monkeypatch.setattr(main, "call_llm", spy)
    plain, observed = main.create_tutor(observed=False), main.create_tutor(observed=True)
    # 装饰的是同一业务方法对象，没有复制或修改方法体。
    for method in ("answer_course", "organize_notes", "tutor"):
        assert getattr(observed, method).__wrapped__.__func__ is getattr(plain, method).__func__
    imports = [n for n in ast.walk(ast.parse(inspect.getsource(business)))
               if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert all("agenttrace" not in ast.unparse(node) for node in imports)

    def execute(agent):
        try:
            return ("result", agent.tutor("什么是软件工程？", stream=stream, fail_notes=fail_notes))
        except RuntimeError as error:
            return (type(error), str(error))

    at.init(db)
    try:
        expected = execute(plain)
        expected_calls = calls.copy()
        assert not db.exists()  # 未接入的业务路径不会启动观测或创建数据库。
        calls.clear()
        assert execute(observed) == expected
        assert calls == expected_calls
        assert at.flush()
        assert QueryStore(db).list_traces()["items"][0]["status"] == (
            "failed" if fail_notes else "success"
        )
    finally:
        at.shutdown()
