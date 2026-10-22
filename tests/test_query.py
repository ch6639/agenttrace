"""查询层验收：过滤、分页、统计、只读连接。"""

import sqlite3

import pytest

from agenttrace.query import QueryStore
from agenttrace.seed import seed_database


def test_seed_is_repeatable_and_query_paginates(tmp_path):
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    assert seed_database(a, n=20, seed=42) == 20
    seed_database(b, n=20, seed=42)
    qa, qb = QueryStore(a), QueryStore(b)
    assert qa.list_traces() == qb.list_traces()
    result = qa.list_traces(limit=3, offset=2, order="tokens", direction="asc")
    assert result["total"] == 20 and len(result["items"]) == 3
    assert all(t["span_count"] == 6 for t in result["items"])
    assert result["items"][0]["total_tokens"] <= result["items"][1]["total_tokens"]
    # 重复 seed 是 UPSERT，不复制同一批数据。
    seed_database(a, n=20, seed=42)
    assert qa.list_traces()["total"] == 20


def test_filters_detail_and_statistics(tmp_path):
    db = tmp_path / "seed.db"
    seed_database(db, n=100, seed=42)
    query = QueryStore(db)
    all_rows = query.list_traces(limit=100)["items"]
    failed = query.list_traces(status="failed", limit=100)
    assert 0 < failed["total"] < 100
    assert all(t["status"] == "failed" for t in failed["items"])
    start = all_rows[0]["start_time"]
    assert query.list_traces(t_from=start, t_to=start)["total"] == 1
    detail = query.get_trace(all_rows[0]["id"])
    assert len(detail["spans"]) == 6
    assert isinstance(detail["spans"][0]["input"], dict)
    assert query.get_trace("missing") is None
    stats = query.stats()
    assert stats["total_traces"] == 100
    assert stats["total_tokens"] == sum(t["total_tokens"] for t in all_rows)
    assert stats["success_rate"] == (100 - failed["total"]) / 100
    assert stats["avg_steps"] == 6
    assert sum(stats["failure_types"].values()) == failed["total"]
    with query.connect() as conn, pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM traces")


def test_empty_database_and_invalid_sort(tmp_path):
    db = tmp_path / "not_created.db"
    query = QueryStore(db)
    assert query.list_traces() == {"total": 0, "items": []}
    assert query.stats()["success_rate"] == 0
    assert query.get_trace("missing") is None
    assert not db.exists()
    with pytest.raises(ValueError):
        query.list_traces(order="DROP TABLE traces")
