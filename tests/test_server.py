"""FastAPI 契约与边界：TC-10/15，空态和错误数据库。"""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from agenttrace.seed import seed_database  # noqa: E402
from server.main import create_app  # noqa: E402


def test_api_contract_validation_and_filters(tmp_path):
    db = tmp_path / "api.db"
    seed_database(db, n=15)
    with TestClient(create_app(db)) as client:
        assert client.get("/api/health").json()["ok"]
        rows = client.get("/api/traces?limit=3&order=tokens&dir=asc").json()
        assert rows["total"] == 15 and len(rows["items"]) == 3
        trace_id = rows["items"][0]["id"]
        detail = client.get(f"/api/traces/{trace_id}")
        assert detail.status_code == 200 and len(detail.json()["spans"]) == 6
        assert client.get("/api/traces/missing").status_code == 404
        assert client.get("/api/stats").json()["total_traces"] == 15
        for query in ("limit=0", "offset=-1", "status=other", "order=malicious", "dir=bad",
                      "t_from=nonsense", "t_from=2026-09-01T00:00:00",
                      "t_from=2026-09-02T00:00:00Z&t_to=2026-09-01T00:00:00Z"):
            assert client.get("/api/traces?" + query).status_code == 422
        # 同一时刻的不同时区写法应命中同样的数据。
        rows = client.get("/api/traces", params={"t_from": "2026-09-01T08:00:00+08:00",
                                               "t_to": "2026-09-01T00:00:00Z"}).json()
        assert rows["total"] == 1


def test_empty_and_corrupted_database(tmp_path):
    db = tmp_path / "empty.db"
    with TestClient(create_app(db)) as client:
        assert client.get("/api/traces").json() == {"total": 0, "items": []}
        assert client.get("/api/stats").json()["total_tokens"] == 0
        assert not db.exists()
        db.write_text("invalid sqlite", encoding="utf-8")
        response = client.get("/api/traces")
        assert response.status_code == 503
        assert "invalid sqlite" not in response.text


def test_static_viewer_and_missing_build(tmp_path):
    web = tmp_path / "web"
    web.mkdir()
    with TestClient(create_app(tmp_path / "empty.db", web_dir=web)) as client:
        assert client.get("/").status_code == 503
        (web / "index.html").write_text("<h1>AgentTrace</h1>", encoding="utf-8")
        (web / "assets").mkdir()
        (web / "assets" / "main.js").write_text("console.log('ok')", encoding="utf-8")
        for route in ("/", "/stats", "/traces/123"):
            assert "AgentTrace" in client.get(route).text
        assert client.get("/assets/main.js").status_code == 200
        assert client.get("/assets/missing.js").status_code == 404
