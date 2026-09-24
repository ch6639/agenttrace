"""命令行入口：`agenttrace ui`（Web 查看器）与 `agenttrace seed`（造数）。

骨架阶段仅注册子命令并提示未实现；按 AT-DES-001 §4.3 的计划逐步落地：
- ui   —— Sprint 1 垂直切片先立最小版，M3 补全（NFR-4：一条命令启动）
- seed —— Sprint 1 垂直切片实现（兼作 TC-19 的 1000 条 trace 数据源）
"""

from __future__ import annotations

import argparse
import sys

from agenttrace import __version__


def _not_implemented(name: str) -> int:
    print(f"[agenttrace] `{name}` 尚未实现（计划见 docs/M1设计文档 §4.3）", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agenttrace",
        description="AgentTrace —— Agent 运行可观测性工具",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("ui", help="启动 Web 查看器（一条命令，零配置）")
    sub.add_parser("seed", help="生成合成 trace 数据（--n 1000 兼作 TC-19 数据源）")
    args = parser.parse_args(argv)

    if args.command == "ui":
        return _not_implemented("ui")
    if args.command == "seed":
        return _not_implemented("seed")
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
