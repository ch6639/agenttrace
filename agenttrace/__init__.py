"""AgentTrace —— 轻量级 Agent 运行可观测性工具（SDK 包）。

以装饰器/上下文管理器非侵入拦截 Agent 的 LLM 调用、工具调用与决策步骤，
默认逐事件确认写入 SQLite，可选异步批量模式；配合 Web 查看器查询、回放和统计。

已实现公开接口（设计见 docs/M1设计文档-AgentTrace概要设计.md §4.1）：
    trace() / tool() / session() / llm() / log_step()
    flush() / shutdown() / init() / diagnostics()
可选 patch_openai() 尚待实现；显式记录路径不依赖 openai 包。
"""

__version__ = "0.1.0"

from agenttrace.api import diagnostics, flush, init, shutdown  # noqa: E402
from agenttrace.intercept import llm, log_step, session, tool, trace  # noqa: E402

__all__ = [
    "__version__", "flush", "init", "shutdown", "llm", "log_step", "session", "tool", "trace",
    "diagnostics",
]
