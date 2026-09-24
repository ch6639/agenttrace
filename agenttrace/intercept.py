"""拦截器（agenttrace/intercept，FR-1.2/FR-2.1/FR-2.2）。

职责（AT-DES-001 §2.2/§4.1）：
- @trace / @tool 装饰器：入队占位 span，异常透传并记 status=failed + status_message；
- llm() 上下文管理器：返回 SpanHandle，流式收尾一次 update()（§5.3）；
- patch_openai()：可选 monkey-patch openai>=1.x 的 chat.completions.create（含
  stream=True）；未安装 openai 或兼容性检测失败 → 静默跳过/警告 no-op（D-01）；
- capture_input/capture_output 开关（NFR-6 脱敏）。

Sprint 1 任务 5 按 TDD 实现。
"""
