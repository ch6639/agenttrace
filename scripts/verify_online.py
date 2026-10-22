"""TC-17：三个有输出上限的真实请求；报告不保存密钥、响应正文或底层错误信息。"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import UTC, datetime
from functools import partial
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


def check_demo(db: Path, *, max_tokens: int) -> dict:
    """TC-17 完整双工具真实链路；每次 LLM 调用都使用输出上限。"""
    from examples.demo_agent.main import ask_llm, create_tutor

    agent = create_tutor()
    agent.ask_llm = partial(ask_llm, max_tokens=max_tokens)
    result, caught = {}, None
    at.init(db)
    try:
        result = agent.tutor("软件测试的目的是什么？请简短回答。", mock=False, stream=True)
    except Exception as error:
        caught = type(error).__name__
    finally:
        persisted = at.flush()
        at.shutdown()
    query = QueryStore(db)
    traces = query.list_traces()["items"]
    detail = query.get_trace(traces[0]["id"]) if len(traces) == 1 else None
    spans = detail["spans"] if detail else []
    tools = [span for span in spans if span["type"] == "tool"]
    llms = [span for span in spans if span["type"] == "llm"]
    tool_by_id = {span["id"]: span for span in tools}
    llm_by_tool = {tool_by_id[span["parent_id"]]["name"]: span for span in llms
                   if span["parent_id"] in tool_by_id}
    roots = [span for span in spans if span["parent_id"] is None]
    routes = [span for span in spans if span["type"] == "step" and span["name"] == "route"]
    root_id = roots[0]["id"] if len(roots) == 1 else None
    tree_valid = (
        root_id is not None and roots[0]["name"] == "course_tutor"
        and len(routes) == 1 and routes[0]["parent_id"] == root_id
        and len(tools) == 2 and all(tool["parent_id"] == root_id for tool in tools)
        and len(llm_by_tool) == 2
        and all(span["trace_id"] == detail["trace"]["id"] for span in spans)
    )
    tools_complete = (
        len(tools) == 2 and {tool["name"] for tool in tools}
        == {"answer_course", "organize_notes"}
        and all(tool["status"] == "success" for tool in tools)
    )
    outputs_match = (
        bool(result.get("answer")) and bool(result.get("notes"))
        and llm_by_tool.get("answer_course", {}).get("output") == result.get("answer")
        and llm_by_tool.get("organize_notes", {}).get("output") == result.get("notes")
    )
    secret_absent = os.environ["OPENAI_API_KEY"] not in json.dumps(detail, ensure_ascii=False)
    total_tokens = detail["trace"]["total_tokens"] if detail else None
    tokens_match = total_tokens == sum(span["token_in"] + span["token_out"] for span in llms)
    all_streaming = len(llms) == 2 and all(span["is_stream"] for span in llms)
    passed = (
        caught is None and persisted and detail is not None and len(spans) == 6
        and detail["trace"]["status"] == "success"
        and all(span["status"] == "success" for span in spans)
        and tools_complete and tree_valid and outputs_match and tokens_match
        and all_streaming and secret_absent
    )
    return {
        "case": "course_tutor_demo", "passed": bool(passed), "flush_ok": persisted,
        "error_type": caught, "trace_status": detail["trace"]["status"] if detail else None,
        "tool_count": len(tools), "llm_count": len(llms), "span_count": len(spans),
        "tools_complete": tools_complete, "outputs_match": bool(outputs_match),
        "tree_valid": tree_valid,
        "all_streaming": all_streaming, "tokens_match": tokens_match,
        "total_tokens": total_tokens, "secret_absent": secret_absent,
        "estimated": any("__estimated__" in (span.get("status_message") or "") for span in llms),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-tokens", type=int, default=64, choices=range(1, 129),
                        metavar="1..128")
    parser.add_argument("--output", type=Path, default=Path("docs/online-acceptance.json"))
    parser.add_argument("--demo-only", action="store_true", help="仅验收真实双工具链路，两次请求")
    args = parser.parse_args(argv)
    missing = [name for name in ("OPENAI_API_KEY", "OPENAI_MODEL") if not os.getenv(name)]
    report = {"measured_at": datetime.now(UTC).isoformat(), "cases": [],
              "requests_planned": 2 if args.demo_only else 3,
              "max_tokens_per_request": args.max_tokens,
              "limitations": ["client_cancel 是调用方主动取消，不冒充服务端断流故障",
                              "取消时 usage 可能缺失，允许明确标记的 Token 估算"]}
    if missing:
        report.update(status="blocked", missing_configuration=missing)
        exit_code = 2
    else:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        with tempfile.TemporaryDirectory(prefix="agenttrace-online-") as temporary:
            if args.demo_only:
                report["cases"] = [check_demo(Path(temporary) / "demo.db",
                                             max_tokens=args.max_tokens)]
            else:
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
