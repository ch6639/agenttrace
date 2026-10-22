"""造数与查看器入口；可选服务端依赖仅在 ui 子命令加载。"""

from __future__ import annotations

import argparse
import sqlite3
import sys

from agenttrace import __version__


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("必须大于 0")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agenttrace",
        description="AgentTrace —— Agent 运行可观测性工具",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")
    ui = sub.add_parser("ui", help="启动本地 Web 查看器")
    ui.add_argument("--db", default="./agenttrace.db")
    ui.add_argument("--host", default="127.0.0.1")
    ui.add_argument("--port", type=positive_int, default=8320)
    seed = sub.add_parser("seed", help="生成合成 trace 数据（--n 1000 兼作 TC-19 数据源）")
    seed.add_argument("--db", default="./agenttrace.db")
    seed.add_argument("--n", type=positive_int, default=1000)
    seed.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)

    if args.command == "ui":
        if args.port > 65535:
            parser.error("port 必须小于等于 65535")
        try:
            import uvicorn

            from server.main import create_app
        except ImportError:
            print('[agenttrace] 查看器需要可选依赖：pip install "agenttrace[server]"',
                  file=sys.stderr)
            return 2
        uvicorn.run(create_app(args.db), host=args.host, port=args.port)
        return 0
    if args.command == "seed":
        from agenttrace.seed import seed_database
        try:
            count = seed_database(args.db, n=args.n, seed=args.seed)
        except (OSError, ValueError, sqlite3.Error) as error:
            print(f"[agenttrace] 造数失败：{type(error).__name__}", file=sys.stderr)
            return 1
        print(f"已写入 {count} 条合成 trace：{args.db}")
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
