"""在全新 venv 中安装 wheel，并通过实际 HTTP 检查 CLI、API 和所有前端资源。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import subprocess
import tempfile
import time
import venv
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener


def verify(python: Path, directory: Path) -> dict:
    db = directory / "installed.db"
    command = [str(python), "-I", "-m", "agenttrace.cli"]
    subprocess.run(command + ["--version"], cwd=directory, check=True, timeout=20)
    subprocess.run(command + ["seed", "--db", str(db), "--n", "1000"],
                   cwd=directory, check=True, timeout=30)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    # 从仓库外、隔离解释器运行安装后的正式 CLI，不能回退导入源码包。
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(
            command + ["ui", "--db", str(db), "--port", str(port)],
            cwd=directory, env=env, stdout=log, stderr=log,
        )
        try:
            base = f"http://127.0.0.1:{port}"
            client = build_opener(ProxyHandler({}))  # 本地回环地址不经过系统代理。

            def get(path):
                with client.open(base + path, timeout=5) as response:
                    return response.read()

            deadline = time.monotonic() + 20
            while True:
                try:
                    assert json.loads(get("/api/health"))["ok"]
                    break
                except URLError:
                    if process.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError("installed CLI did not become healthy") from None
                    time.sleep(0.05)
            listing = json.loads(get("/api/traces?limit=1000"))
            assert listing["total"] == len(listing["items"]) == 1000
            trace_id = listing["items"][0]["id"]
            assert len(json.loads(get(f"/api/traces/{trace_id}"))["spans"]) == 6
            assert json.loads(get("/api/stats"))["total_traces"] == 1000
            index = get("/").decode("utf-8")
            assert get("/stats").decode("utf-8") == index
            assert get(f"/traces/{trace_id}").decode("utf-8") == index
            assets = set(re.findall(r'(?:src|href)="(/assets/[^\"]+)"', index))
            assert assets
            for asset in list(assets):
                data = get(asset)
                assert data
                if asset.endswith(".js"):
                    for chunk in re.findall(r'["\'](?:\./)?([^/"\']+\.js)["\']',
                                            data.decode("utf-8")):
                        assets.add("/assets/" + chunk)
            for asset in assets:
                assert get(asset)
            return {"status": "passed", "measured_at": datetime.now(UTC).isoformat(),
                    "traces": 1000, "assets_checked": sorted(assets),
                    "checks": ["installed CLI", "real HTTP health/list/detail/stats",
                               "SPA routes", "entry assets and lazy JS chunks"],
                    "limitations": ["HTTP 资源检查不替代浏览器渲染验收"]}
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    parser.add_argument("--python", type=Path, help="复用已创建的独立验收 venv")
    parser.add_argument("--output", type=Path, default=Path("docs/install-acceptance.json"))
    args = parser.parse_args()
    wheel = args.wheel.resolve()
    with tempfile.TemporaryDirectory(prefix="agenttrace-install-") as temporary:
        directory = Path(temporary)
        python = args.python.resolve() if args.python else None
        if python is None:
            environment = directory / "venv"
            venv.create(environment, with_pip=True)
            python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            constraints = Path(__file__).resolve().parents[1] / "requirements-dev.lock"
            subprocess.run(
                [str(python), "-m", "pip", "install", "--constraint", str(constraints),
                 str(wheel) + "[server]"], check=True, timeout=180,
            )
        report = verify(python, directory)
        report.update(wheel_sha256=hashlib.sha256(wheel.read_bytes()).hexdigest(),
                      wheel_size_bytes=wheel.stat().st_size)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(f"clean installation + HTTP smoke: PASS; report: {args.output}")


if __name__ == "__main__":
    main()
