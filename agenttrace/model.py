"""数据模型（agenttrace/model，FR-2.x）。

职责（AT-DES-001 §2.2/§3）：
- Trace / Span 数据类（字段与 models/class-model.puml、DDL 定稿一致）；
- input/output JSON 序列化（不可序列化对象转 repr）；
- 单字段超 64KiB 截断并置 __truncated__ 标记（D-08）；
- 业务时间（start/end）与入库时间（created_at）分离；duration 用 monotonic 计算。

Sprint 1 任务 1（TDD：tests/test_model.py 先行）。
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# 观测类型（AT-DES-001 §3.1：llm/tool 有起止时间，step 为瞬时事件无时长）
TYPE_LLM = "llm"
TYPE_TOOL = "tool"
TYPE_STEP = "step"

STATUS_RUNNING = "running"
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"

MAX_FIELD_BYTES = 64 * 1024  # D-08：input/output 单字段截断上限


def new_id() -> str:
    return str(uuid.uuid4())


def utc_now_iso() -> str:
    """ISO 8601 UTC，毫秒精度（AT-DES-001 §3.3 时间约定）。"""
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _diff_ms(start: str, end: str) -> int:
    s = datetime.fromisoformat(start)
    e = datetime.fromisoformat(end)
    return max(0, int((e - s).total_seconds() * 1000))


def estimate_tokens(text: str) -> int:
    """token 近似计数 len//4（D-08：响应自带 usage 时应优先使用真实值）。"""
    return len(text) // 4


def dumps(value: Any) -> str:
    """JSON 序列化；不可序列化对象回退 repr；超 64KiB 截断并标记（D-08）。

    截断结果保持合法 JSON：{"__truncated__": true, "head": "<前缀>"}。
    """
    try:
        text = json.dumps(value, ensure_ascii=False, default=repr)
    except (TypeError, ValueError):  # 循环引用等极端情况
        text = json.dumps(repr(value), ensure_ascii=False)
    if len(text.encode("utf-8")) > MAX_FIELD_BYTES:
        raw = text.encode("utf-8")[: MAX_FIELD_BYTES - 512]  # 预留包裹结构开销
        head = raw.decode("utf-8", errors="ignore")
        return json.dumps({"__truncated__": True, "head": head}, ensure_ascii=False)
    return text


@dataclass
class Trace:
    """一次 Agent 任务运行的完整记录（根聚合）。"""

    name: str
    id: str = field(default_factory=new_id)
    session_id: str | None = None  # 会话分组预留（本期 UI 不使用）
    status: str = STATUS_RUNNING
    start_time: str = field(default_factory=utc_now_iso)
    end_time: str | None = None
    total_tokens: int = 0
    latency_ms: int | None = None
    error: str | None = None
    created_at: str = field(default_factory=utc_now_iso)

    def finish(
        self,
        status: str,
        *,
        error: str | None = None,
        total_tokens: int | None = None,
    ) -> None:
        self.status = status
        if error is not None:
            self.error = error
        if total_tokens is not None:
            self.total_tokens = total_tokens
        self.end_time = utc_now_iso()
        self.latency_ms = _diff_ms(self.start_time, self.end_time)

    def to_row(self) -> dict[str, Any]:
        """展平为 spans/traces DDL 列名对应的行（供存储层 UPSERT）。"""
        return {
            "id": self.id,
            "name": self.name,
            "session_id": self.session_id,
            "status": self.status,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "total_tokens": self.total_tokens,
            "latency_ms": self.latency_ms,
            "error": self.error,
            "created_at": self.created_at,
        }


@dataclass
class Span:
    """Trace 内的单个事件节点：llm 调用 / 工具调用 / 瞬时决策步骤。"""

    trace_id: str
    parent_id: str | None
    type: str
    name: str
    id: str = field(default_factory=new_id)
    status: str = STATUS_RUNNING
    input: str | None = None  # 已序列化的 JSON 字符串（dumps 产物）
    output: str | None = None
    status_message: str | None = None  # 异常/状态说明，与 output 分离（决策 #2）
    model: str | None = None  # 仅 llm 类型
    token_in: int = 0
    token_out: int = 0
    start_time: str = field(default_factory=utc_now_iso)
    end_time: str | None = None
    duration_ms: int | None = None  # step 类型为空（瞬时事件）
    is_stream: bool = False  # 流式 LLM 响应标记（FR-2.5）
    created_at: str = field(default_factory=utc_now_iso)
    _mono_start: int = field(default_factory=time.monotonic_ns, repr=False, compare=False)

    def finish(
        self,
        status: str,
        *,
        output: str | None = None,
        status_message: str | None = None,
        token_in: int | None = None,
        token_out: int | None = None,
    ) -> None:
        """写终态并计算时长（monotonic，不受系统改时影响，AT-DES-001 §3.3）。

        占位 span（status=running）先落盘、结束/流式收尾时 update 写回
        （AT-DES-000 决策 #4，FR-2.5 的实现基础）。
        """
        self.status = status
        if output is not None:
            self.output = output
        if status_message is not None:
            self.status_message = status_message
        if token_in is not None:
            self.token_in = token_in
        if token_out is not None:
            self.token_out = token_out
        self.end_time = utc_now_iso()
        self.duration_ms = max(0, (time.monotonic_ns() - self._mono_start) // 1_000_000)

    def to_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "trace_id": self.trace_id,
            "parent_id": self.parent_id,
            "type": self.type,
            "name": self.name,
            "input": self.input,
            "output": self.output,
            "status": self.status,
            "status_message": self.status_message,
            "model": self.model,
            "token_in": self.token_in,
            "token_out": self.token_out,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": self.duration_ms,
            "is_stream": int(self.is_stream),
            "created_at": self.created_at,
        }
