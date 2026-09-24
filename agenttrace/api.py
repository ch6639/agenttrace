"""公开接口与初始化（agenttrace/api，FR-1.1）。

职责（AT-DES-001 §2.2/§4.1）：
- 暴露 @trace / @tool / llm() / log_step / flush / shutdown / patch_openai / init；
- init()：db_path 默认 ./agenttrace.db（D-09）、enabled 开关、全局配置；
- 注册 atexit 钩子，进程退出前自动 flush（FR-2.4 兜底）。

Sprint 1 任务 5 按 TDD 实现。
"""
