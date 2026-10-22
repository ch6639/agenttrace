"""TC-17：三个有输出上限的真实请求；报告不保存密钥、响应正文或底层错误信息。"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import agenttrace as at
from agenttrace.query import QueryStore


def check_case(db: Path, *, stream: bool, cancel: bool, max_tokens: int) -> dict:
    from examples.demo_agent.main import call_llm

    parts, usage = [], {}
    expected = "failed" if cancel else "success"
    caught = None
    interrupted = False
    prompt = "Reply with a short sentence about software testing."
    at.init(db)
    events = call_llm(
        prompt, mock=False,
        stream=stream, client=None, model=os.environ["OPENAI_MODEL"], max_tokens=max_tokens,
    )
    try:
        with at.session("online-cancel" if cancel else "online-stream" if stream else "online"):
            with at.llm(model=os.environ["OPENAI_MODEL"], input=prompt,
                        is_stream=stream) as handle:
                try:
                    for event in events:
                        if event.get("delta"):
                            parts.append(event["delta"])
                            if cancel:
                                interrupted = True
                                raise InterruptedError("client cancelled after first content delta")
                        if event.get("usage"):
                            usage = event["usage"]
                finally:
                    events.close()
                    handle.update(output="".join(parts), token_in=usage.get("prompt_tokens"),
                                  token_out=usage.get("completion_tokens"))
    except Exception as error:
        caught = type(error).__name__  # 不输出带 URL、凭据或响应正文的异常消息。
    finally:
        persisted = at.flush()
        at.shutdown()
    query = QueryStore(db)
    traces = query.list_traces()["items"]
    detail = query.get_trace(traces[0]["id"]) if traces else None
    spans = [span for span in detail["spans"] if span["type"] == "llm"] if detail else []
    span = spans[0] if len(spans) == 1 else {}
    key = os.environ["OPENAI_API_KEY"]
    secret_absent = key not in json.dumps(detail, ensure_ascii=False)
    passed = (
        persisted and bool(parts) and len(spans) == 1 and secret_absent
        and detail["trace"]["status"] == span.get("status") == expected
        and span.get("output") == "".join(parts)
        and span.get("is_stream") == stream
        and ((cancel and interrupted and caught == "InterruptedError")
             or (not cancel and caught is None))
        and span.get("token_in", 0) >= 0 and span.get("token_out", 0) >= 0
        and detail["trace"]["total_tokens"] == span.get("token_in", 0) + span.get("token_out", 0)
    )
    return {
        "case": "client_cancel" if cancel else "stream" if stream else "non_stream",
        "passed": passed, "flush_ok": persisted, "error_type": caught,
        "trace_status": detail["trace"]["status"] if detail else None,
        "has_output": bool(parts), "secret_absent": secret_absent,
        "usage_returned": bool(usage),
        "estimated": "__estimated__" in (span.get("status_message") or ""),
        "total_tokens": detail["trace"]["total_tokens"] if detail else None,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-tokens", type=int, default=64, choices=range(1, 129),
                        metavar="1..128")
    parser.add_argument("--output", type=Path, default=Path("docs/online-acceptance.json"))
    args = parser.parse_args(argv)
    missing = [name for name in ("OPENAI_API_KEY", "OPENAI_MODEL") if not os.getenv(name)]
    report = {"measured_at": datetime.now(UTC).isoformat(), "cases": [],
              "requests_planned": 3, "max_tokens_per_request": args.max_tokens,
              "limitations": ["client_cancel 是调用方主动取消，不冒充服务端断流故障",
                              "取消时 usage 可能缺失，允许明确标记的 Token 估算"]}
    if missing:
        report.update(status="blocked", missing_configuration=missing)
        exit_code = 2
    else:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        with tempfile.TemporaryDirectory(prefix="agenttrace-online-") as temporary:
            report["cases"] = [
                check_case(Path(temporary) / f"{stream}-{cancel}.db", stream=stream,
                           cancel=cancel, max_tokens=args.max_tokens)
                for stream, cancel in ((False, False), (True, False), (True, True))
            ]
        exit_code = 0 if all(case["passed"] for case in report["cases"]) else 1
        report["status"] = "passed" if exit_code == 0 else "failed"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(f"online acceptance: {report['status']}; report: {args.output}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
