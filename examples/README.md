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

TC-16：离线双工具已通过跨进程集成测试。2026-10-22 用户在本机运行 DeepSeek 真实验收，
普通/SSE/主动取消三种模型调用通过，见 [真实报告](../docs/deepseek-acceptance.json)。
TC-17 仍需完整双工具业务在线结果，不能以独立 LLM 调用代替完整 Agent 流程。

在线验收工具：在本机设置上述环境变量后执行 `python -m scripts.verify_online`，依次检查
普通响应、SSE 完整响应、首段文本后的调用方取消。总计三个请求，每次默认最多 64 个输出
Token（可用 `--max-tokens 1..128`）；服务需支持 `max_tokens` 和流式接口。报告只保存状态、
Token 与布尔检查项，不保存密钥或回答正文。配置缺失返回 2，验收失败返回 1，全通过返回 0。
密钥应在本机配置，勿写入源码、提交数据库或发送到聊天。

## DeepSeek 本机接入

当前默认配置：`https://api.deepseek.com` + `deepseek-flash`，按官方 Chat Completions
接口调用。官方文档核对日期为 2026-10-22：
[首次调用](https://api-docs.deepseek.com/) /
[接口参数](https://api-docs.deepseek.com/api/create-chat-completion/)。

在项目仓库根目录打开终端，执行：

```powershell
.venv\Scripts\python.exe scripts\verify_deepseek.py
```

在隐藏输入提示后粘贴 DeepSeek API Key 并按回车。密钥不回显，不作为命令参数，不写入配置
文件或验收报告；仅供本次 Python 进程使用，结束后还原原有环境变量。
如该进程已设置 `DEEPSEEK_API_KEY`，脚本使用它；不会自动取用原有 `OPENAI_API_KEY`。
脚本默认完成三个真实请求，每个最多 64 个输出 Token；账号需要有可用的 API 调用权限。

成功结果写入 `docs/deepseek-acceptance.json`，包含三种调用的状态、Token、持久化与脱敏
检查结果。无需向聊天发送密钥；运行后告知已完成，即可从该报告核对在线验收结果。

完整双工具业务验收另行执行：

```powershell
.venv\Scripts\python.exe scripts\verify_deepseek.py --demo
```

此模式仅执行“课程答疑 → 笔记整理”两个真实请求，每次最多 64 个输出 Token，检查六个
Span、两个工具、两次流式 LLM 的父子关系、业务输出和 Token 汇总。
结果单独写入 `docs/deepseek-demo-acceptance.json`，不覆盖三项调用的已有报告。

无密钥、无网络的脚本预检：

```powershell
.venv\Scripts\python.exe scripts\verify_deepseek.py --check-config
```

预检强制不提供密钥，只生成 `docs/deepseek-preflight.json`；预期状态 `blocked`、退出码 2，
不能作为在线通过的证据。真实执行可用 `--model MODEL` 或 `--max-tokens 128` 调整参数。
入口使用 Python，可直接在 Windows 默认 PowerShell 策略下运行，无需调整执行策略。

本示例对官方 `api.deepseek.com` 域名显式传入 `thinking.type=disabled`，保持普通文本验收
和短输出预算；其他兼容服务不附加该参数。Mock 默认路径保持离线。也可在同一终端自行
设置三个 `OPENAI_*` 配置，再用 `--real --stream` 运行 Demo；正式验收优先使用有输出上限
的 `--demo` 入口。

## 非侵入基线与持久模式

`business.py` 不导入 AgentTrace。`main.create_tutor(observed=False)` 运行未接入版本；
`observed=True` 对同一实例的原始方法加装饰器并包裹统一 LLM 助手，不修改业务方法体。
`tests/test_noninvasive.py` 对照同步/流式、成功/失败四种组合，核对输出、异常及调用顺序。

默认逐事件确认写入（`--durability durable`）；如需异步批量，可显式传
`--durability async`，但该模式不满足强杀只丢最后一条的持久性承诺。
