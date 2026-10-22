"""可选 FastAPI 只读 API + 查看器静态文件托管。"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse

from agenttrace import __version__
from agenttrace.query import QueryStore


def create_app(db_path: str | Path = "./agenttrace.db", *, web_dir: Path | None = None):
    app = FastAPI(title="AgentTrace", version=__version__)
    store = QueryStore(db_path)
    static = web_dir or Path(__file__).parent / "static"

    @app.exception_handler(sqlite3.Error)
    async def database_error(request, exc):
        return JSONResponse(status_code=503, content={"detail": "本地数据库不可读或结构不兼容"})

    def time_window(t_from, t_to):
        def normalize(value):
            if value is None:
                return None
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    raise ValueError("timezone required")
                return parsed.astimezone(UTC).isoformat(timespec="milliseconds")
            except ValueError as error:
                raise HTTPException(422, "时间必须是含时区的 ISO 8601 格式") from error
        start, end = normalize(t_from), normalize(t_to)
        if start and end and start > end:
            raise HTTPException(422, "开始时间不能晚于结束时间")
        return start, end

    @app.get("/api/health")
    def health():
        # 不在健康检查中创建数据库；若已有库则实际验证可读性。
        store.list_traces(limit=1)
        return {"ok": True, "db": str(store.path), "version": __version__}

    @app.get("/api/traces")
    def traces(
        status: Literal["running", "success", "failed"] | None = None,
        t_from: str | None = None, t_to: str | None = None,
        order: Literal["start_time", "latency", "tokens"] = "start_time",
        dir: Literal["asc", "desc"] = "desc",
        limit: int = Query(50, ge=1, le=1000), offset: int = Query(0, ge=0),
    ):
        start, end = time_window(t_from, t_to)
        return store.list_traces(status=status, t_from=start, t_to=end, order=order,
                                 direction=dir, limit=limit, offset=offset)

    @app.get("/api/traces/{trace_id}")
    def detail(trace_id: str):
        result = store.get_trace(trace_id)
        if result is None:
            raise HTTPException(404, "Trace 不存在")
        return result

    @app.get("/api/stats")
    def stats(t_from: str | None = None, t_to: str | None = None):
        start, end = time_window(t_from, t_to)
        return store.stats(t_from=start, t_to=end)

    @app.get("/", include_in_schema=False)
    @app.get("/stats", include_in_schema=False)
    @app.get("/traces/{trace_id}", include_in_schema=False)
    def index(trace_id: str | None = None):
        if not (static / "index.html").is_file():
            return JSONResponse(status_code=503, content={
                "detail": "查看器尚未构建，请在 web/ 执行 npm ci && npm run build；API 文档见 /docs"
            })
        return FileResponse(static / "index.html")

    @app.get("/assets/{asset_path:path}", include_in_schema=False)
    def asset(asset_path: str):
        asset_root = (static / "assets").resolve()
        target = (asset_root / asset_path).resolve()
        if not target.is_relative_to(asset_root) or not target.is_file():
            raise HTTPException(404)
        return FileResponse(target)

    return app
