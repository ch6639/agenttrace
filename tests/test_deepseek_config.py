"""DeepSeek 本地配置：离线预检、隐藏输入、凭据隔离与环境恢复。"""

import json
import os
import sys
from pathlib import Path

from scripts import verify_deepseek, verify_online


def test_preflight_never_calls_network_even_with_configured_keys(tmp_path, monkeypatch):
    from examples.demo_agent import main as demo

    monkeypatch.setenv("OPENAI_API_KEY", "other-provider-secret")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test")
    monkeypatch.setenv("OPENAI_MODEL", "original-model")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-configured-secret")
    previous = dict(os.environ)
    monkeypatch.setattr(demo, "urlopen", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("preflight must not access network"),
    ))
    output = tmp_path / "preflight.json"
    assert verify_deepseek.main(["--check-config", "--output", str(output)]) == 2
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "blocked" and report["cases"] == []
    assert report["missing_configuration"] == ["OPENAI_API_KEY"]
    assert report["model"] == "deepseek-flash" and report["thinking"] == "disabled"
    assert all(os.environ.get(name) == value for name, value in previous.items())
    assert set(os.environ) == set(previous)


def test_hidden_key_is_used_temporarily_and_not_written_to_report(tmp_path, monkeypatch):
    key = "deepseek-hidden-secret"
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "original-other-provider-secret")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(verify_deepseek.getpass, "getpass", lambda prompt: key)
    previous = dict(os.environ)

    def fake_verify(argv):
        assert os.environ["OPENAI_API_KEY"] == key
        assert os.environ["OPENAI_BASE_URL"] == "https://api.deepseek.com"
        assert os.environ["OPENAI_MODEL"] == "deepseek-flash"
        output = Path(argv[argv.index("--output") + 1])
        output.write_text('{"status":"passed","cases":[]}', encoding="utf-8")
        return 0

    monkeypatch.setattr(verify_online, "main", fake_verify)
    output = tmp_path / "report.json"
    assert verify_deepseek.main(["--output", str(output)]) == 0
    assert key not in output.read_text(encoding="utf-8")
    assert all(os.environ.get(name) == value for name, value in previous.items())
    assert set(os.environ) == set(previous)


def test_noninteractive_mode_does_not_reuse_another_provider_key(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "original-other-provider-secret")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    output = tmp_path / "report.json"
    assert verify_deepseek.main(["--output", str(output)]) == 2
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "blocked"
    assert "original-other-provider-secret" not in capsys.readouterr().out


def test_failure_restores_environment_and_hides_error_details(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-configured-secret")
    previous = dict(os.environ)

    def fake_verify(argv):
        raise RuntimeError("request failed deepseek-configured-secret")

    monkeypatch.setattr(verify_online, "main", fake_verify)
    assert verify_deepseek.main(["--output", str(tmp_path / "report.json")]) == 1
    assert all(os.environ.get(name) == value for name, value in previous.items())
    assert set(os.environ) == set(previous)
    assert "deepseek-configured-secret" not in capsys.readouterr().out
