"""CLI 参数与命令分派；不在单元测试中启动阻塞服务器。"""

import pytest

from agenttrace.cli import main


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert "0.1.0" in capsys.readouterr().out


def test_ui_dispatch(tmp_path, monkeypatch) -> None:
    uvicorn = pytest.importorskip("uvicorn")
    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.append(kwargs))
    assert main(["ui", "--db", str(tmp_path / "ui.db"), "--port", "8321"]) == 0
    assert calls == [{"host": "127.0.0.1", "port": 8321}]


def test_seed_command(tmp_path) -> None:
    from agenttrace.query import QueryStore
    db = tmp_path / "seed.db"
    assert main(["seed", "--n", "12", "--db", str(db)]) == 0
    assert QueryStore(db).list_traces()["total"] == 12


def test_invalid_seed_size():
    with pytest.raises(SystemExit) as error:
        main(["seed", "--n", "0"])
    assert error.value.code == 2


def test_no_command_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert "usage: agenttrace" in capsys.readouterr().out
