"""在线验收工具自身使用协议替身验证；不将这些测试算作真实在线验收。"""

import json

import pytest

from examples.demo_agent import main as demo
from scripts.verify_online import check_case, main


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
