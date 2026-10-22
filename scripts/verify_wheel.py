"""检查 wheel 资源，并用隔离解释器验证 SDK 无第三方依赖即可工作。"""

from __future__ import annotations

import argparse
import email
import subprocess
import sys
import zipfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel")
    args = parser.parse_args()
    with zipfile.ZipFile(args.wheel) as archive:
        names = archive.namelist()
        assert "agenttrace/data/mock_responses.jsonl" in names
        assert "server/static/index.html" in names, "先构建前端，再打 wheel"
        assert any(n.startswith("server/static/assets/") and n.endswith(".js") for n in names)
        built = Path(__file__).resolve().parents[1] / "server" / "static" / "assets"
        required = {
            "server/static/assets/" + file.name for file in built.glob("*") if file.is_file()
        }
        assert required <= set(names), "安装包遗漏构建资源（含异步图表 chunk）"
        packaged = {name for name in names if name.startswith("server/static/assets/")}
        assert packaged == required, "安装包混入旧构建资源，请用 scripts/build_wheel.py 干净构建"
        metadata = email.message_from_bytes(archive.read(next(
            n for n in names if n.endswith(".dist-info/METADATA")
        )))
        # 所有依赖必须受 extra 条件约束，SDK 本体不强制安装 FastAPI 等。
        requirements = metadata.get_all("Requires-Dist", [])
        assert all("extra ==" in requirement for requirement in requirements)
    code = """
import sqlite3, sys, tempfile
from contextlib import closing
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]).resolve()))
import agenttrace as at
from agenttrace.mock import MockClient
assert '.whl' in at.__file__
assert MockClient().complete('mock-tutor', 'question')['output']
with tempfile.TemporaryDirectory() as tmp:
    db = Path(tmp) / 'smoke.db'
    at.init(db)
    with at.session('wheel-smoke'):
        with at.llm(model='mock') as gen:
            gen.update(output='ok', token_in=1, token_out=2)
    assert at.flush()
    at.shutdown()
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute('SELECT total_tokens FROM traces').fetchone()[0] == 3
assert 'fastapi' not in sys.modules
print('wheel resources + isolated zero-dependency SDK smoke: PASS')
"""
    subprocess.run([sys.executable, "-I", "-S", "-c", code, args.wheel], check=True, timeout=30)


if __name__ == "__main__":
    main()
