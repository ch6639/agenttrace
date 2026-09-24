"""CLI 骨架测试：--version、未实现子命令的退出码、无参数时打印帮助。"""

import pytest

from agenttrace.cli import main


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert "0.1.0" in capsys.readouterr().out


def test_ui_not_implemented_yet() -> None:
    assert main(["ui"]) == 2


def test_seed_not_implemented_yet() -> None:
    assert main(["seed"]) == 2


def test_no_command_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert "usage: agenttrace" in capsys.readouterr().out
