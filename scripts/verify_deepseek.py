"""本机隐藏输入 DeepSeek 密钥并运行验收；不保存密钥，不复用其他平台的密钥。"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import warnings
from pathlib import Path

BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-config", action="store_true", help="强制无密钥、无网络预检")
    parser.add_argument("--max-tokens", type=int, default=64, choices=range(1, 129),
                        metavar="1..128")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    project = Path(__file__).resolve().parents[1]
    if str(project) not in sys.path:
        sys.path.insert(0, str(project))
    from scripts.verify_online import main as verify

    report_name = "deepseek-preflight.json" if args.check_config else "deepseek-acceptance.json"
    output = args.output or project / "docs" / report_name
    names = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL")
    previous = {name: os.environ.get(name) for name in names}
    try:
        key = ""
        if not args.check_config:
            key = os.getenv("DEEPSEEK_API_KEY", "")
            if not key and sys.stdin.isatty():
                with warnings.catch_warnings():
                    # 隐藏输入不可用时停止，不降级为可回显输入。
                    warnings.simplefilter("error", getpass.GetPassWarning)
                    key = getpass.getpass("DeepSeek API key (hidden, used only for this run): ")
            elif not key:
                print("请在本机交互终端运行此命令，或配置 DEEPSEEK_API_KEY；当前不发出请求。")
        os.environ.update(OPENAI_API_KEY=key.strip(), OPENAI_BASE_URL=BASE_URL,
                          OPENAI_MODEL=args.model)
        if key.strip():
            print(f"DeepSeek: 3 requests, max {args.max_tokens} output tokens each.")
        result = verify(["--max-tokens", str(args.max_tokens), "--output", str(output)])
        report = json.loads(output.read_text(encoding="utf-8"))
        report.update(provider="deepseek", model=args.model, base_url=BASE_URL,
                      thinking="disabled")
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        return result
    except (KeyboardInterrupt, EOFError):
        print("已取消，未保存密钥。")
        return 2
    except Exception as error:
        print(f"DeepSeek 验收未完成：{type(error).__name__}")
        return 1
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


if __name__ == "__main__":
    raise SystemExit(main())
