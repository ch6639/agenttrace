"""可复现的合成 trace，直接批量写库，避免造数挤满 SDK 有界队列。"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from agenttrace.model import Span, Trace, dumps
from agenttrace.storage import Storage


def seed_database(db_path: str | Path, *, n: int = 1000, seed: int = 42) -> int:
    if n < 0:
        raise ValueError("n must be non-negative")
    rng = random.Random(seed)
    storage = Storage(db_path)
    traces, spans = [], []
    try:
        for i in range(n):
            new_id = lambda: str(UUID(int=rng.getrandbits(128), version=4))  # noqa: E731
            start = datetime(2026, 9, 1, tzinfo=UTC) + timedelta(seconds=i * 60)
            latency = rng.randint(100, 2500)
            failed = rng.random() < 0.2
            trace = Trace(
                name=f"合成课程助手 #{i + 1}", id=new_id(),
                status="failed" if failed else "success",
                start_time=start.isoformat(timespec="milliseconds"),
                end_time=(start + timedelta(milliseconds=latency)).isoformat(
                    timespec="milliseconds"
                ),
                latency_ms=latency, error="RuntimeError: 合成工具失败" if failed else None,
                created_at=start.isoformat(timespec="milliseconds"),
            )
            root_id, tool_id, notes_id = new_id(), new_id(), new_id()
            specs = [
                (root_id, None, "step", "course_tutor", 0),
                (new_id(), root_id, "step", "route", 1),
                (tool_id, root_id, "tool", "answer_course", 10),
                (new_id(), tool_id, "llm", "answer_llm", 20),
                (notes_id, root_id, "tool", "organize_notes", latency // 2),
                (new_id(), notes_id, "llm", "notes_llm", latency // 2 + 10),
            ]
            for sid, parent, kind, name, offset in specs:
                is_failure = failed and name in ("course_tutor", "organize_notes", "notes_llm")
                span = Span(
                    trace_id=trace.id, parent_id=parent, type=kind, name=name, id=sid,
                    input=dumps({"question": "什么是软件工程？"}),
                    output=dumps("系统化的软件开发方法。"),
                    status="failed" if is_failure else "success",
                    status_message="RuntimeError: 合成工具失败" if is_failure else None,
                    model="mock-tutor" if kind == "llm" else None,
                    token_in=rng.randint(20, 100) if kind == "llm" else 0,
                    token_out=rng.randint(30, 150) if kind == "llm" else 0,
                    start_time=(start + timedelta(milliseconds=offset)).isoformat(
                        timespec="milliseconds"
                    ),
                    end_time=trace.end_time, duration_ms=max(0, latency - offset),
                    is_stream=kind == "llm", created_at=trace.created_at,
                )
                if name == "route":
                    span.duration_ms = None
                    span.end_time = span.start_time
                trace.total_tokens += span.token_in + span.token_out
                spans.append(span.to_row())
            traces.append(trace.to_row())
            if len(traces) >= 100:
                storage.write_batch(traces, spans)
                traces, spans = [], []
        storage.write_batch(traces, spans)
    finally:
        storage.close()
    return n
