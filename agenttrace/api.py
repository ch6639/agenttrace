"""SDK 生命周期：延迟初始化、零配置、旁路失效和退出排空。"""

from __future__ import annotations

import atexit
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from agenttrace.pipeline import Pipeline
from agenttrace.storage import Storage


@dataclass
class Runtime:
    storage: Storage
    pipeline: Pipeline


_lock = threading.RLock()
_runtime: Runtime | None = None
_db_path: str | Path = "./agenttrace.db"
_enabled = True
_failed = False
_durability = "durable"
_last_stats: dict[str, int] = {}


def init(
    db_path: str | Path = "./agenttrace.db", *, enabled: bool = True,
    durability: str = "durable",
) -> None:
    """配置后延迟建库。应在任务开始前调用，不能在活跃 trace 中重新配置。"""
    global _db_path, _enabled, _failed, _durability, _last_stats
    if durability not in ("durable", "async"):
        raise ValueError("durability must be 'durable' or 'async'")
    with _lock:
        if not shutdown():
            return  # 超时后不能用新配置复用旧后台写入线程。
        _db_path, _enabled, _failed = db_path, enabled, False
        _durability, _last_stats = durability, {}


def get_runtime() -> Runtime | None:
    global _runtime, _failed
    with _lock:
        if not _enabled or _failed:
            return None
        if _runtime is None:
            storage = None
            try:
                storage = Storage(_db_path, durable=_durability == "durable")
                _runtime = Runtime(storage, Pipeline(storage, durability=_durability).start())
            except Exception:
                if storage is not None:
                    storage.close()
                _failed = True
                print("[agenttrace] 存储初始化失败，已禁用本次记录", file=sys.stderr)
        return _runtime


def flush(timeout: float = 5.0) -> bool:
    """等待提交；超时或本轮运行存在丢弃/写失败时返回 False。"""
    with _lock:
        runtime = _runtime
        failed = _failed
        last_stats = dict(_last_stats)
    if runtime is None:
        return (not failed and not last_stats.get("dropped")
                and not last_stats.get("failed_batches"))
    drained = runtime.pipeline.flush(timeout)
    stats = runtime.pipeline.stats
    return drained and not stats["dropped"] and not stats["failed_batches"]


def shutdown(timeout: float = 5.0) -> bool:
    """停止后台写入并关库；超时保留连接，避免后台线程使用已关闭的库。"""
    global _runtime, _last_stats
    with _lock:
        if _runtime is None:
            return True
        if not _runtime.pipeline.shutdown(timeout):
            print("[agenttrace] shutdown 超时，后台写入尚未结束", file=sys.stderr)
            return False
        _runtime.storage.close()
        _last_stats = _runtime.pipeline.stats
        _runtime = None
        return True


def diagnostics() -> dict:
    """本轮初始化后的摄入状态；shutdown 后仍保留最终统计，不包含事件或凭据。"""
    with _lock:
        stats = _runtime.pipeline.stats if _runtime else _last_stats
        return {
            "enabled": _enabled, "initialized": _runtime is not None,
            "durability": _durability, "initialization_failed": _failed,
            "queued": stats.get("queued", 0), "dropped": stats.get("dropped", 0),
            "failed_batches": stats.get("failed_batches", 0),
            "written_events": stats.get("written_events", 0),
        }


atexit.register(shutdown)
