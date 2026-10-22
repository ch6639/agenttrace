# AgentTrace —— Agent 运行可观测性工具

轻量级 Agent 可观测性工具：**Python SDK 非侵入拦截 + SQLite 单文件存储 + Web 时间线回放**，
面向"单机、离线、零配置"的个人开发者场景，让 Agent 运行过程可见、可查、可回放、可量化。

> 《软件工程》课程项目实践作业（2026 秋）· 单人 + AI 协同开发 · SRS/设计文档见 [docs/](docs/)

[![CI](https://github.com/ch6639/agenttrace/actions/workflows/ci.yml/badge.svg)](https://github.com/ch6639/agenttrace/actions/workflows/ci.yml)

## 核心功能（对应 SRS F1~F6）

| 编号 | 功能 | 一句话说明 |
|------|------|-----------|
| F1 | 非侵入拦截 SDK | 装饰器/上下文管理器接入，任务入口 ≤3 行，不改业务逻辑 |
| F2 | Trace 数据模型 | 一次任务 = 一条 trace；llm/tool/step 三类事件树形组织 |
| F3 | SQLite 持久化 | 单文件零部署，进程重启后可查，按时间/状态过滤 |
| F4 | Web 查看器 | 列表页 + 时间线详情（瀑布图、失败标红、完整输入/输出） |
| F5 | 统计总览 | 总 Token、成功率、平均步数、失败类型分布 |
| F6 | 示例 Agent | "课程学习助手"双工具，Mock 模式离线可演示 |

## 架构

```
被测 Agent 进程 ──≤3 行接入──▶ agenttrace SDK（拦截/上下文栈/内存队列）
                                        │ 默认逐事件确认；可选异步批量（WAL）
                                        ▼
                              SQLite（agenttrace.db 单文件）
                                        ▲ 只读
              agenttrace ui ─▶ FastAPI(/api/*) + Vue 3 查看器
```

组件图/类图/时序图源文件见 [models/](models/)（PlantUML，可渲染导出）。

## 快速开始

```bash
# SDK 与查看器（Python 3.11+；SDK 本身零第三方依赖）
python -m pip install -r requirements-dev.lock
python -m pip install -e ".[dev,server]"

# 查看器只需构建一次；Node.js 22.12+ 或 24+，npm
cd web
npm ci
npm run build
cd ..

# 离线课程学习助手：无密钥、无网络
python -m examples.demo_agent.main --stream

# 一条命令启动已构建的查看器 → http://127.0.0.1:8320
agenttrace ui

# 可选：合成数据；相同 seed 重复执行会更新同一批 ID
agenttrace seed --n 1000

# 本地验收（CI 同款）
ruff check .
pytest --cov=agenttrace --cov=server --cov-fail-under=70
```

当前可用的 SDK 用法：

```python
import agenttrace

@agenttrace.tool()
def answer(question):
    with agenttrace.llm(model="my-model", input=question) as gen:
        result = "示例回答"  # 替换为你的实际 LLM 调用
        gen.update(output=result, token_in=10, token_out=5)
    return result

@agenttrace.trace()                 # 任务入口：import + 装饰器共 2 行
def tutor(question):
    agenttrace.log_step("route", {"next": "answer"})
    return answer(question)

tutor("软件工程是什么？")
assert agenttrace.flush()           # 阻塞至写盘完成；超时/丢弃/写失败返回 False
```

也支持 `with agenttrace.session("task"):`，以及同步/异步函数装饰器。`llm()` 始终返回
SpanHandle，**不会替你调用 LLM**；流式调用需在 with 内聚合增量文本，并在 finally 中
`gen.update()` 以保存中断前的部分输出，示例见 `examples/demo_agent/main.py`。

## 当前进度（2026-10-22）

已打通 SDK → 离线双工具 Agent → SQLite → FastAPI → Vue 三视图。查询 API 支持时间/状态
过滤、排序和分页；查看器提供失败标红、流式详情、Token/成功率/平均事件数/失败类型统计。
94 项 Python 测试通过，SDK + 服务端覆盖率 95.17%；生产前端构建通过。
远程 CI 已全绿：Ubuntu/Windows × Python 3.11/3.12，以及 Viewer 构建与隔离安装验收，
见 [实际运行](https://github.com/ch6639/agenttrace/actions/runs/39750948864)。
默认 durable 已通过提交前/中/后子进程强杀和独立连接可读测试；补齐非侵入基线、并发隔离、
过载告警与依赖快照。首屏 JS 由 1.47 MB 降至约 490 KB，统计图按需加载。
本轮增加 Token 差值汇总、独立虚拟环境安装及实际 HTTP 验收、三请求在线验收工具。
最新证据与剩余缺口见 [阶段收尾记录](docs/阶段收尾与验收记录-2026-10-22.md)。

注意验收边界：默认 durable 在健康存储下于记录调用返回前确认提交；可选 async 不保证强杀
只丢最后一条。本轮 30 次矩阵中 durable 的 20 ms 多步骤场景开销约 70.21%，200 ms
场景约 8.86%，短任务尚未达到 10%；不将算法优化冒充整体性能达标。
DeepSeek 真实普通/SSE/主动取消三项调用已通过；双工具业务全链路、可选
`patch_openai()` 和课程交付材料仍待完成。实际在线证据见
[DeepSeek 报告](docs/deepseek-acceptance.json)。

## 运行约定

DeepSeek 接入：在本机终端执行
`.venv\Scripts\python.exe scripts\verify_deepseek.py`，在隐藏提示中输入密钥即可。
脚本配置 `deepseek-flash` 与官方地址，执行三个有输出上限的验收请求；密钥不写入报告。
双工具业务验收使用 `.venv\Scripts\python.exe scripts\verify_deepseek.py --demo`，
仅执行“答疑 → 笔记整理”的两次模型请求，报告另存，保留已有三项接口验收证据。
接入说明与无网络预检见 [示例说明](examples/README.md#deepseek-本机接入)。

- `--db` 可指定 SQLite 文件；默认 SDK、示例和 CLI 均用当前目录的 `agenttrace.db`。
- 默认 `durability="durable"`：WAL + FULL，根 Trace/Span 原子提交；不依赖 atexit 才落盘。
  `agenttrace.init(durability="async")` 可显式选择批量模式以降低业务延迟，接受强杀可能丢多条。
- `agenttrace.diagnostics()` 返回模式、丢弃、写失败和写入计数，shutdown 后保留最终统计；
  存储故障时业务继续，但 flush 返回 False，不把故障写入计作持久性成功。
- 默认只监听 `127.0.0.1:8320`，查看器不依赖 CDN 或联网服务。
- 首次从源码安装需要构建前端；之后 `agenttrace ui` 一条命令启动。发布前先构建前端，再运行
  `python scripts/build_wheel.py` 和 `python scripts/verify_wheel.py dist/agenttrace-0.1.0-py3-none-any.whl`。
  干净暂存构建可防止旧哈希资源混入安装包。
- `python scripts/verify_install.py dist/agenttrace-0.1.0-py3-none-any.whl` 在新 venv 安装
  wheel 及锁定的 server 依赖，从仓库外运行 CLI，以实际 HTTP 验证千条查询和静态资源。
  首次安装依赖需要联网；可用 `--python PATH` 复用独立验收 venv。
- 在线验收：本机配置 `OPENAI_API_KEY`、`OPENAI_MODEL` 和可选 `OPENAI_BASE_URL` 后，执行
  `python -m scripts.verify_online`；共三个请求，每次最多 64 个输出 Token，可用
  `--max-tokens 1..128` 调整。缺少配置退出码为 2，不视为通过。报告不包含响应正文或密钥。
  取消场景验证调用方主动中断；不冒充服务端断流，协议异常仍由离线测试覆盖。
- `capture_input=False` / `capture_output=False` 可关闭捕获；敏感键、Bearer、常见密钥格式和
  任务开始时配置的密钥环境变量会被基础脱敏。这不替代业务侧敏感信息控制。
- 缺失 usage 时用 `len(text)//4` 估算，并在事件状态说明标记 `__estimated__`；不将 Token
  数自动换算成金额。超过 64 KiB 的字段有 `__truncated__` 标记。
- 独立 `@tool` / `llm()` / `log_step` 在无活动 trace 时不建库、不记录；任务应从 `trace` / `session` 进入。
- 异步子任务须在根任务结束前 await；线程池上下文传播、生成器装饰器不在当前支持范围。
- `init()` 应在任务开始前调用。正常进程退出有 atexit 排空；SIGKILL/强制终止不执行 atexit。

## 仓库结构

```
agenttrace/        SDK 包（api/intercept/context/model/pipeline/storage/mock/cli）
server/            FastAPI 只读查询 + 构建后的静态文件（agenttrace ui 启动）
web/               Vue 3 + Element Plus + ECharts 查看器
examples/          demo agent（课程答疑 + 笔记整理）；应答打包在 agenttrace/data/
tests/             pytest 单元 + 集成测试
docs/              SRS / M1 设计文档 / 项目计划 / 借鉴分析
models/            PlantUML 模型源文件（组件/类/时序/用例/功能结构）
scripts/           可复现的性能初测脚本
.github/workflows/ CI（lint + test + coverage，ubuntu & windows 双平台）
```

## 开发约定

- 单人 Scrum + TDD：先测试后实现；**CI 全绿方可合并**（NFR-5）；
- 覆盖率门槛：Sprint 1 核心模块落地后启用 `--cov-fail-under=70`（TC-22）；
- SDK 保持零第三方依赖（openai 补丁为可选路径）；演示/测试不依赖真实 LLM API；
- 需求变更走 SRS 修订记录，需求-用例-测试追踪（附录 B）同步更新。

## 文档索引

- [需求规格说明书 SRS V0.7](docs/SRS初稿-AgentTrace.md)
- [M1 概要设计文档（AT-DES-001）](docs/M1设计文档-AgentTrace概要设计.md)
- [项目计划](docs/项目计划-Agent可观测性工具.md)
- [Langfuse 设计借鉴分析（AT-DES-000）](docs/设计研究-Langfuse借鉴分析.md)
- [需求调研记录](docs/需求调研-访谈提纲与痛点记录.md)
- [接手检查、缺口、测试映射与开发记录](docs/接手检查与验收记录-2026-10-15.md)
- [本轮完善、持久性验收与性能矩阵](docs/完善与验收记录-2026-10-15.md)
- [M2/M3 阶段收尾与验收记录](docs/阶段收尾与验收记录-2026-10-22.md)
