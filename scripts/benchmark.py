"""可复现的 TC-18/19 初测；结果是指定负载的测量，不替代完整性能验收。"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import tempfile
import time
from pathlib import Path

import agenttrace as at
from agenttrace.query import QueryStore
from agenttrace.seed import seed_database


def run(iterations: int, delay_ms: float, durability: str = "durable") -> dict:
    def workload(question):
        time.sleep(delay_ms / 1000)
        return {"answer": question * 2}

    observed = at.trace()(workload)

    def measure(func):
        samples = []
        for _ in range(iterations):
            start = time.perf_counter_ns()
            func("软件工程的需求如何验证？")
            samples.append((time.perf_counter_ns() - start) / 1_000_000)
        return statistics.mean(samples)

    with tempfile.TemporaryDirectory(prefix="agenttrace-benchmark-") as tmp:
        db = Path(tmp) / "performance.db"
        at.init(db, durability=durability)
        observed("warmup")
        assert at.flush()
        baseline = measure(workload)
        instrumented = measure(observed)
        persisted = at.flush()
        at.shutdown()
        query_db = Path(tmp) / "query.db"
        seed_database(query_db, n=1000)
        query = QueryStore(query_db)
        query.list_traces(limit=1000)
        samples = []
        for _ in range(10):
            start = time.perf_counter_ns()
            result = query.list_traces(limit=1000)
            samples.append((time.perf_counter_ns() - start) / 1_000_000)
        return {
            "python": platform.python_version(), "platform": platform.platform(),
            "iterations": iterations, "workload_delay_ms": delay_ms,
            "durability": durability,
            "baseline_mean_ms": round(baseline, 4),
            "instrumented_mean_ms": round(instrumented, 4),
            "overhead_percent": round((instrumented / baseline - 1) * 100, 2),
            "flush_ok": persisted, "query_total_rows": result["total"],
            "query_1000_mean_ms": round(statistics.mean(samples), 4),
            "query_1000_max_ms": round(max(samples), 4),
            "scope": "单个入口 trace，模拟等待负载；只读查询不含 HTTP、浏览器或图表渲染",
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--delay-ms", type=float, default=20)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--durability", choices=["durable", "async"], default="durable")
    args = parser.parse_args()
    if args.iterations < 1 or args.delay_ms < 0:
        parser.error("iterations 必须 >0；delay-ms 必须 >=0")
    result = run(args.iterations, args.delay_ms, args.durability)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
