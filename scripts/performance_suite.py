"""TC-18/19 场景矩阵：两种持久模式、入口/多步骤、短/中/长任务。

不依赖真实 API；按真实测量结果报告阈值，不因超标隐藏场景或降低要求。
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

import agenttrace as at
from agenttrace.query import QueryStore
from agenttrace.seed import seed_database


def summarize(samples):
    ordered = sorted(samples)
    return {"mean_ms": round(statistics.mean(samples), 4),
            "p95_ms": round(ordered[int((len(ordered) - 1) * 0.95)], 4),
            "max_ms": round(max(samples), 4)}


def measure_case(db, *, mode, profile, delay_ms, iterations):
    def answer(question):
        if delay_ms:
            time.sleep(delay_ms / (2000 if profile == "multi_step" else 1000))
        return "回答：" + question

    def notes(text):
        if delay_ms:
            time.sleep(delay_ms / 2000)
        return {"notes": text}

    def observe_call(func):
        @at.tool(func.__name__)
        def wrapper(value):
            with at.llm(model="benchmark", input=value) as gen:
                result = func(value)
                gen.update(output=result, token_in=10, token_out=20)
            return result
        return wrapper

    if profile == "entry_only":
        plain, observed = answer, at.trace("benchmark")(answer)
    else:
        def workflow(question, answer_fn, notes_fn, log=None):
            if log:
                log("route", {"tools": ["answer", "notes"]})
            return notes_fn(answer_fn(question))

        traced_answer, traced_notes = observe_call(answer), observe_call(notes)
        plain = lambda question: workflow(question, answer, notes)  # noqa: E731
        observed = at.trace("benchmark")(
            lambda question: workflow(question, traced_answer, traced_notes, at.log_step)
        )
    baseline, instrumented = [], []
    at.init(db, durability=mode)
    try:
        assert observed("预热") == plain("预热")
        assert at.flush()
        for index in range(iterations):
            # 交错并交替先后顺序，减少固定先跑基线导致的偏差。
            cases = [(plain, baseline), (observed, instrumented)]
            for func, samples in cases if index % 2 == 0 else reversed(cases):
                started = time.perf_counter_ns()
                result = func("需求如何验证？")
                samples.append((time.perf_counter_ns() - started) / 1_000_000)
                assert result
            if index % 10 == 9:
                assert at.flush()  # 计时外排空，避免纯 CPU 场景挤满可选 async 队列。
        started = time.perf_counter_ns()
        persisted = at.flush()
        drain_ms = (time.perf_counter_ns() - started) / 1_000_000
        diagnostics = at.diagnostics()
    finally:
        at.shutdown()
    base, traced = statistics.mean(baseline), statistics.mean(instrumented)
    overhead = (traced / base - 1) * 100
    return {"durability": mode, "profile": profile, "workload_delay_ms": delay_ms,
            "iterations": iterations, "baseline": summarize(baseline),
            "instrumented": summarize(instrumented),
            "extra_mean_ms": round(traced - base, 4), "overhead_percent": round(overhead, 2),
            "within_10_percent": overhead <= 10, "flush_ok": persisted,
            "final_drain_ms": round(drain_ms, 4), "diagnostics": diagnostics}


def query_measurements(db):
    seed_database(db, n=1000)
    query = QueryStore(db)
    samples = []
    for _ in range(10):
        started = time.perf_counter_ns()
        result = query.list_traces(limit=1000)
        samples.append((time.perf_counter_ns() - started) / 1_000_000)
    assert len(result["items"]) == 1000
    metrics = {"rows": 1000, "spans_per_trace": 6, "sqlite": summarize(samples)}
    try:
        from fastapi.testclient import TestClient

        from server.main import create_app
    except ImportError:
        metrics["asgi_test_client"] = {"unavailable": True}
    else:
        samples = []
        with TestClient(create_app(db)) as client:
            for _ in range(10):
                started = time.perf_counter_ns()
                response = client.get("/api/traces?limit=1000")
                body = response.json()
                samples.append((time.perf_counter_ns() - started) / 1_000_000)
                assert response.status_code == 200 and len(body["items"]) == 1000
        metrics["asgi_test_client"] = summarize(samples)
    return metrics


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--output", type=Path, default=Path("docs/performance-comparison.json"))
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("iterations 必须 >0")
    cases = []
    with tempfile.TemporaryDirectory(prefix="agenttrace-suite-") as tmp:
        for mode in ("durable", "async"):
            for profile in ("entry_only", "multi_step"):
                for delay in (0, 20, 200):
                    result = measure_case(
                        Path(tmp) / f"{mode}-{profile}-{delay}.db", mode=mode,
                        profile=profile, delay_ms=delay, iterations=args.iterations,
                    )
                    cases.append(result)
                    print(f"{mode}/{profile}/{delay}ms: {result['overhead_percent']}%", flush=True)
        queries = query_measurements(Path(tmp) / "query.db")
    report = {
        "measured_at": datetime.now(UTC).isoformat(),
        "python": platform.python_version(), "platform": platform.platform(),
        "cases": cases, "query": queries,
        "limitations": ["模拟等待负载，无真实 LLM；冷/热浏览器和网络传输需另测",
                        "async 计时为业务调用延迟，排空在计时外，且不提供强杀丢失上限",
                        "单机短时采样受调度/磁盘/并发进程影响，不代表所有工作负载"],
    }
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(f"saved: {args.output}")


if __name__ == "__main__":
    main()
