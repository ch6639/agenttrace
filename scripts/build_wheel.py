"""从干净暂存目录构建 wheel，避免 setuptools 增量目录混入旧的哈希资源。"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("dist"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if not (root / "server" / "static" / "index.html").is_file():
        parser.error("先在 web/ 执行 npm ci 和 npm run build")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="agenttrace-wheel-") as temporary:
        staging = Path(temporary)
        for filename in ("pyproject.toml", "README.md"):
            shutil.copy2(root / filename, staging / filename)
        for package in ("agenttrace", "server"):
            shutil.copytree(root / package, staging / package,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        subprocess.run(
            [sys.executable, "-m", "pip", "wheel", str(staging), "--no-deps", "-w", str(output)],
            check=True,
        )


if __name__ == "__main__":
    main()
