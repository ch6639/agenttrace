"""在线验收工具自身使用协议替身验证；不将这些测试算作真实在线验收。"""

import json

import pytest

from examples.demo_agent import main as demo
from scripts.verify_online import check_case, check_demo, main


@pytest.mark.parametrize("stream,cancel", [(False, False), (True, False), (True, True)])
def test_online_gate_checks_persistence_usage_and_client_cancellation(
    tmp_path, monkeypatch, stream, cancel,
):
    monkeypatch.setenv("OPENAI_API_KEY", "acceptance-secret-123456")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return json.dumps({"choices": [{"message": {"content": "answer"}}],
                               "usage": {"prompt_tokens": 2, "completion_tokens": 3}}).encode()

        def __iter__(self):
            for event in ({"choices": [{"delta": {"content": "answer"}}]},
                          {"usage": {"prompt_tokens": 2, "completion_tokens": 3}}):
                yield ("data: " + json.dumps(event) + "\n").encode()
            yield b"data: [DONE]\n"

    def transport(request, timeout):
        assert json.loads(request.data)["max_tokens"] == 64
        assert timeout == 30
        return Response()

    monkeypatch.setattr(demo, "urlopen", transport)
    result = check_case(tmp_path / "online.db", stream=stream, cancel=cancel, max_tokens=64)
    assert result["passed"]
    assert result["estimated"] == cancel
    assert result["usage_returned"] != cancel
    assert "acceptance-secret" not in json.dumps(result)


def test_missing_configuration_is_blocked_instead_of_passed(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    output = tmp_path / "online.json"
    assert main(["--output", str(output)]) == 2
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "blocked" and not report["cases"]


def test_transport_failure_does_not_leak_error_details(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "acceptance-secret-123456")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")

    def transport(*args, **kwargs):
        raise RuntimeError("request failed acceptance-secret-123456")

    monkeypatch.setattr(demo, "urlopen", transport)
    result = check_case(tmp_path / "failed.db", stream=False, cancel=False, max_tokens=64)
    assert not result["passed"] and result["error_type"] == "RuntimeError"
    assert result["secret_absent"]
    assert "acceptance-secret" not in json.dumps(result)


@pytest.mark.parametrize("fail_notes", [False, True])
def test_live_demo_gate_checks_two_tools_and_bounded_calls(tmp_path, monkeypatch, fail_notes):
    monkeypatch.setenv("OPENAI_API_KEY", "demo-gate-secret-123456")
    monkeypatch.setenv("OPENAI_MODEL", "deepseek-flash")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.deepseek.com")
    calls = []

    class Response:
        def __init__(self, text):
            self.text = text

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def __iter__(self):
            for event in (
                {"choices": [{"delta": {"content": self.text}, "finish_reason": None}]},
                {"choices": [{"delta": {}, "finish_reason": "stop"}],
                 "usage": {"prompt_tokens": 2, "completion_tokens": 3}},
            ):
                yield ("data: " + json.dumps(event) + "\n").encode()
            yield b"data: [DONE]\n"

    def transport(request, timeout):
        body = json.loads(request.data)
        assert body["max_tokens"] == 64 and body["stream"]
        calls.append(body)
        if len(calls) == 2 and fail_notes:
            raise RuntimeError("notes failed demo-gate-secret-123456")
        return Response("answer" if len(calls) == 1 else "notes")

    monkeypatch.setattr(demo, "urlopen", transport)
    result = check_demo(tmp_path / "demo.db", max_tokens=64)
    assert len(calls) == 2 and result["tool_count"] == result["llm_count"] == 2
    assert "answer" in calls[1]["messages"][0]["content"]
    assert result["passed"] != fail_notes
    assert result["secret_absent"]
    if not fail_notes:
        assert result["span_count"] == 6 and result["total_tokens"] == 10
        assert result["tools_complete"] and result["outputs_match"] and result["tokens_match"]
        assert result["tree_valid"]
        assert not result["estimated"]
    assert "demo-gate-secret" not in json.dumps(result)
