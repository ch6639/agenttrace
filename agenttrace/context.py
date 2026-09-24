"""上下文栈（agenttrace/context，FR-2.3）。

职责（AT-DES-001 §5.2）：
- contextvars.ContextVar 维护"当前 span 栈"，@trace/@tool/with llm() 进入即 push、
  退出即 pop，parent_id 自动取栈顶；
- asyncio task 继承上下文副本，协程内嵌套关系正确；
- 已知限制：跨线程（线程池）不传播，本期非目标（demo Agent 不使用线程池）。

Sprint 1 任务 2 按 TDD 实现。
"""
