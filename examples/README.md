# examples —— 示例被测 Agent（演示载体，FR-6）

"课程学习助手"双工具 Agent：

- **工具一：课程答疑** —— LLM 依据课程资料回答提问；
- **工具二：笔记整理** —— 将答疑内容归纳为结构化学习笔记。

约定（AT-DES-001 D-02）：LLM 调用统一收敛到内部 `ask_llm()` 助手函数，
`with agenttrace.llm()` 只包裹该处调用语句——严格满足 TC-02"业务逻辑零改动"口径。

## 运行模式

- **Mock 模式（默认演示）**：`AGENTTRACE_MOCK=1 python examples/demo_agent/main.py`，
  使用 `examples/mock/responses.jsonl` 预存应答，离线可跑（无网络/无密钥）；
- **真实 API**：配置 OpenAI 兼容接口密钥后切换。

TC-16/TC-17 验收：双工具在 Mock 与真实 API 两种模式下均产生有效 trace。
