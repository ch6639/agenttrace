"""旁路失效与隐私开关的端到端回归。"""

import sqlite3

import agenttrace as at
from agenttrace import api
from agenttrace.storage import Storage


def test_storage_write_failure_is_visible_but_business_continues(tmp_path, monkeypatch, capsys):
    def fail(*args):
        raise sqlite3.OperationalError("secret-error-that-must-not-be-logged")

    monkeypatch.setattr(Storage, "write_batch", fail)
    at.init(tmp_path / "fault.db")
    try:
        @at.trace()
        def business():
            return "业务照常完成"

        assert business() == "业务照常完成"
        assert at.flush() is False
        message = capsys.readouterr().err
        assert "存储写入失败" in message
        assert "secret-error" not in message
    finally:
        at.shutdown()
    assert at.diagnostics()["failed_batches"] > 0
    assert at.flush() is False  # shutdown 不得清除已发生的数据丢失信号。


def test_disabled_capture_does_not_inspect_objects(tmp_path):
    class Sensitive:
        def __repr__(self):
            raise AssertionError("capture disabled must not inspect this value")

    at.init(tmp_path / "capture.db")
    try:
        @at.trace(capture_input=False, capture_output=False)
        def task(value):
            return value

        item = Sensitive()
        assert task(item) is item
        assert at.flush()
        with sqlite3.connect(tmp_path / "capture.db") as conn:
            assert conn.execute("SELECT input,output FROM spans").fetchone() == (None, None)
    finally:
        at.shutdown()


def test_shutdown_timeout_keeps_connection_and_configuration(tmp_path, monkeypatch):
    first = tmp_path / "first.db"
    at.init(first)
    with at.session("first"):
        pass
    assert at.flush()
    runtime = api.get_runtime()
    with monkeypatch.context() as patch:
        patch.setattr(runtime.pipeline, "shutdown", lambda timeout: False)
        assert at.shutdown(timeout=0) is False
        at.init(tmp_path / "second.db")
        assert api.get_runtime() is runtime
        assert runtime.storage.db_path == str(first)
    assert at.shutdown()
