"""离线演示和跨进程持久化验收（TC-03/09/16，真实 API 用替身验证适配）。"""

import json
import os
import sqlite3
import subprocess
import sys

import pytest

import agenttrace as at
from agenttrace.mock import MockClient
from examples.demo_agent.main import ask_llm, tutor


def test_mock_replay_per_model_and_stream():
    client = MockClient()
    first = client.complete("mock-tutor", "question")
    second = client.complete("mock-tutor", "notes")
    assert "需求" in first["output"]
    assert "笔记" in second["output"]
    assert first["token_in"] > 0
    assert "".join(client.stream(first)) == first["output"]
    # 每个客户端独立回放；耗尽明确报错，避免伪造第三轮应答。
    with pytest.raises(LookupError):
        client.complete("mock-tutor", "exhausted")
    assert MockClient().complete("mock-tutor", "q") == first


def test_demo_offline_in_subprocess(tmp_path):
    db = tmp_path / "demo.db"
    env = {**os.environ, "AGENTTRACE_MOCK": "1", "OPENAI_API_KEY": "",
           "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run(
        [sys.executable, "-m", "examples.demo_agent.main", "--db", str(db), "--stream"],
        env=env, capture_output=True, text=True, encoding="utf-8", timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "笔记" in result.stdout
    # 新进程退出后，另一个连接仍能读取完整 trace。
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT status FROM traces").fetchone()[0] == "success"
        assert conn.execute("SELECT COUNT(*) FROM spans WHERE type='tool'").fetchone()[0] == 2
        llms = conn.execute(
            "SELECT output,is_stream,token_in,token_out FROM spans WHERE type='llm'"
        ).fetchall()
        assert len(llms) == 2
        assert all(json.loads(row[0]) and row[1] and row[2] > 0 and row[3] > 0 for row in llms)


def test_demo_failure_is_recorded(tmp_path):
    db = tmp_path / "failure.db"
    at.init(db)
    try:
        with pytest.raises(RuntimeError, match="模拟"):
            tutor("软件工程是什么", fail_notes=True)
        assert at.flush()
        with sqlite3.connect(db) as conn:
            assert conn.execute("SELECT status FROM traces").fetchone()[0] == "failed"
            assert conn.execute(
                "SELECT status FROM spans WHERE name='organize_notes'"
            ).fetchone()[0] == "failed"
    finally:
        at.shutdown()


def test_real_mode_adapter_with_fake_transport(tmp_path, monkeypatch):
    from examples.demo_agent import main as demo

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return json.dumps({
                "model": "test-model", "choices": [{"message": {"content": "真实形状"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }).encode()

    def fake_open(request, timeout):
        assert request.full_url == "https://example.test/v1/chat/completions"
        assert request.get_header("Authorization") == "Bearer test-secret-123456"
        assert timeout == 30
        return Response()

    monkeypatch.setenv("OPENAI_API_KEY", "test-secret-123456")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")
    monkeypatch.setattr(demo, "urlopen", fake_open)
    at.init(tmp_path / "real.db")
    try:
        with at.session("real"):
            assert ask_llm("q", mock=False, stream=False, client=None) == "真实形状"
        assert at.flush()
        with sqlite3.connect(tmp_path / "real.db") as conn:
            assert conn.execute("SELECT total_tokens FROM traces").fetchone()[0] == 15
    finally:
        at.shutdown()


@pytest.mark.parametrize("complete", [True, False])
def test_real_sse_adapter_preserves_partial_on_disconnect(tmp_path, monkeypatch, complete):
    from examples.demo_agent import main as demo

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def __iter__(self):
            yield b': keepalive\n'
            yield ('data: ' + json.dumps({
                "choices": [{"delta": {"content": "部分回答"}}],
            }) + '\n').encode()
            if complete:
                yield ('data: ' + json.dumps({
                    "choices": [], "usage": {"prompt_tokens": 8, "completion_tokens": 4},
                }) + '\n').encode()
                yield b'data: [DONE]\n'

    monkeypatch.setenv("OPENAI_API_KEY", "test-secret-123456")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    monkeypatch.setattr(demo, "urlopen", lambda *args, **kwargs: Response())
    db = tmp_path / "sse.db"
    at.init(db)
    try:
        if complete:
            with at.session("sse"):
                assert ask_llm("q", mock=False, stream=True, client=None) == "部分回答"
        else:
            with pytest.raises(RuntimeError, match="中断"), at.session("sse"):
                ask_llm("q", mock=False, stream=True, client=None)
        assert at.flush()
        with sqlite3.connect(db) as conn:
            span = conn.execute(
                "SELECT status,output,token_in,token_out FROM spans WHERE type='llm'"
            ).fetchone()
            assert json.loads(span[1]) == "部分回答"
            assert span[0] == ("success" if complete else "failed")
            if complete:
                assert span[2:] == (8, 4)
    finally:
        at.shutdown()
