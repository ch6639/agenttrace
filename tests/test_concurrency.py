"""默认持久模式下多个业务线程的隔离、完整性及聚合内存回收。"""

from concurrent.futures import ThreadPoolExecutor

import agenttrace as at
from agenttrace import api
from agenttrace.query import QueryStore


def test_parallel_root_tasks_do_not_lose_or_mix_events(tmp_path):
    db = tmp_path / "threads.db"
    at.init(db)
    try:
        @at.trace()
        def task(index):
            at.log_step("index", index)
            with at.llm(model="mock", input=index) as gen:
                gen.update(output=index, token_in=1, token_out=2)
            return index

        with ThreadPoolExecutor(max_workers=4) as pool:
            assert list(pool.map(task, range(40))) == list(range(40))
        assert at.flush()
        query = QueryStore(db)
        traces = query.list_traces(limit=100)
        assert traces["total"] == 40
        for trace in traces["items"]:
            assert trace["status"] == "success" and trace["total_tokens"] == 3
            detail = query.get_trace(trace["id"])
            assert len(detail["spans"]) == 3
            root = next(s for s in detail["spans"] if s["parent_id"] is None)
            assert all(s["parent_id"] == root["id"] for s in detail["spans"] if s != root)
        runtime = api.get_runtime()
        assert not runtime.pipeline._tokens and not runtime.pipeline._span_tokens
        assert at.diagnostics()["dropped"] == 0
    finally:
        at.shutdown()
