"""AgentTrace —— 轻量级 Agent 运行可观测性工具（SDK 包）。

以装饰器/上下文管理器非侵入拦截 Agent 的 LLM 调用、工具调用与决策步骤，
异步批量写入 SQLite，配合 `agenttrace ui` Web 查看器实现运行过程的回放、查询与统计。

公开接口（v1.0，定稿于 docs/M1设计文档-AgentTrace概要设计.md §4.1，Sprint 1 起实现）：
    @agenttrace.trace() / @agenttrace.tool() / with agenttrace.llm(...)
    agenttrace.log_step() / flush() / shutdown() / patch_openai() / init()
"""

__version__ = "0.1.0"
