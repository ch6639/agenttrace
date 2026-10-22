# examples —— 示例被测 Agent（演示载体，FR-6）

"课程学习助手"双工具 Agent：

- **工具一：课程答疑** —— LLM 依据课程资料回答提问；
- **工具二：笔记整理** —— 将答疑内容归纳为结构化学习笔记。

约定（AT-DES-001 D-02）：LLM 调用统一收敛到内部 `ask_llm()` 助手函数，
`with agenttrace.llm()` 只包裹该处调用语句——严格满足 TC-02"业务逻辑零改动"口径。

## 运行模式

- **Mock 模式（默认演示）**：在仓库根目录执行
  `python -m examples.demo_agent.main --stream`，使用 `agenttrace/data/mock_responses.jsonl`
  预存应答，离线可跑（无网络/无密钥）。`AGENTTRACE_MOCK=1` 可强制覆盖 `--real`。
- **失败演示**：`python -m examples.demo_agent.main --fail-notes`，退出码 1，并保留失败 Trace。
- **真实 API**：设置 `OPENAI_API_KEY`、`OPENAI_MODEL` 和可选 `OPENAI_BASE_URL`
  （默认 `https://api.openai.com/v1`），再执行
  `python -m examples.demo_agent.main --real --stream`。兼容 API 需支持 chat completions；
  流式 usage 不可用时由 SDK 标记估算。SDK handle 不代替业务调用。
- **指定文件**：`--db ./agenttrace-demo.db`；查看时使用 `agenttrace ui --db ./agenttrace-demo.db`。

TC-16：离线双工具已通过跨进程集成测试。TC-17：真实非流式/SSE 协议用替身测试通过，
成功流与中断流均有回归；尚未连接真实服务或消费真实密钥，不能标记为在线验收通过。

在线验收工具：在本机设置上述环境变量后执行 `python -m scripts.verify_online`，依次检查
普通响应、SSE 完整响应、首段文本后的调用方取消。总计三个请求，每次默认最多 64 个输出
Token（可用 `--max-tokens 1..128`）；服务需支持 `max_tokens` 和流式接口。报告只保存状态、
Token 与布尔检查项，不保存密钥或回答正文。配置缺失返回 2，验收失败返回 1，全通过返回 0。
密钥应在本机配置，勿写入源码、提交数据库或发送到聊天。

## 非侵入基线与持久模式

`business.py` 不导入 AgentTrace。`main.create_tutor(observed=False)` 运行未接入版本；
`observed=True` 对同一实例的原始方法加装饰器并包裹统一 LLM 助手，不修改业务方法体。
`tests/test_noninvasive.py` 对照同步/流式、成功/失败四种组合，核对输出、异常及调用顺序。

默认逐事件确认写入（`--durability durable`）；如需异步批量，可显式传
`--durability async`，但该模式不满足强杀只丢最后一条的持久性承诺。
