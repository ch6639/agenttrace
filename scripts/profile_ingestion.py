"""可复现摄入剖析：报告同步提交成本，并对照旧全树 Token 重算与差值算法。"""

from __future__ import annotations

import argparse
import cProfile
import json
import platform
import pstats
import statistics
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from agenttrace.model import Span, Trace
from agenttrace.storage import _UPSERT_SPAN, _UPSERT_TRACE, Storage
from scripts.performance_suite import measure_case


class FullScanStorage(Storage):
    """保留优化前算法作为对照，DDL/UPSERT/事务设置与当前实现相同。"""

    def write_batch(self, traces, spans):
        trace_rows, span_rows = list(traces), list(spans)
        with self._conn:
            self._conn.executemany(_UPSERT_TRACE, trace_rows)
            self._conn.executemany(_UPSERT_SPAN, span_rows)
            self._conn.executemany(
                "UPDATE traces SET total_tokens=(SELECT COALESCE(SUM(token_in+token_out),0) "
                "FROM spans WHERE trace_id=traces.id) WHERE id=?",
                [(trace_id,) for trace_id in {row["trace_id"] for row in span_rows}],
            )


def aggregation_case(db, implementation, count):
    storage = implementation(db, durable=False)
    trace = Trace(name="token-aggregation")
    storage.write_batch([trace.to_row()], [])
    rows = [Span(trace_id=trace.id, parent_id=None, type="llm", name=str(index),
                 token_in=1, token_out=2).to_row() for index in range(count)]
    samples = []
    try:
        for row in rows:
            started = time.perf_counter_ns()
            storage.write_batch([], [row])
            samples.append((time.perf_counter_ns() - started) / 1_000_000)
        assert storage._conn.execute("SELECT total_tokens FROM traces").fetchone()[0] == count * 3
    finally:
        storage.close()
    return {"spans": count, "total_ms": round(sum(samples), 3),
            "first_100_mean_ms": round(statistics.mean(samples[:100]), 5),
            "last_100_mean_ms": round(statistics.mean(samples[-100:]), 5)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("docs/ingestion-profile.json"))
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="agenttrace-profile-") as temporary:
        directory = Path(temporary)
        profiler = cProfile.Profile()
        profiler.runcall(measure_case, directory / "durable.db", mode="durable",
                         profile="multi_step", delay_ms=0, iterations=100)
        stats = pstats.Stats(profiler)
        functions = []
        for (filename, line, name), (_, calls, own, cumulative, _) in stats.stats.items():
            functions.append({"file": Path(filename).name, "line": line, "function": name,
                              "calls": calls, "own_ms": round(own * 1000, 3),
                              "cumulative_ms": round(cumulative * 1000, 3)})
        aggregation = {name: aggregation_case(directory / f"{name}.db", implementation, 3000)
                       for name, implementation in (("full_scan", FullScanStorage),
                                                     ("delta", Storage))}
    report = {"measured_at": datetime.now(UTC).isoformat(), "python": platform.python_version(),
              "platform": platform.platform(), "profile_total_ms": round(stats.total_tt * 1000, 3),
              "hot_functions": sorted(functions, key=lambda item: -item["cumulative_ms"])[:25],
              "aggregation_comparison": aggregation,
              "limitations": ["剖析有额外开销，不替代无剖析的性能矩阵",
                              "Token 算法对照使用 WAL+NORMAL 隔离 fsync 成本，"
                              "不能作为默认 durable 性能或持久性证据"]}
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps(aggregation, indent=2))


if __name__ == "__main__":
    main()
