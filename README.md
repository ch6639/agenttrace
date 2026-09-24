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
                                        │ 异步批量 UPSERT（WAL）
                                        ▼
                              SQLite（agenttrace.db 单文件）
                                        ▲ 只读
              agenttrace ui ─▶ FastAPI(/api/*) + Vue 3 查看器
```

组件图/类图/时序图源文件见 [models/](models/)（PlantUML，可渲染导出）。

## 快速开始

```bash
# 开发环境（Python 3.11+）
python -m pip install -e ".[dev]"

# lint 与测试（CI 同款命令）
ruff check .
pytest --cov=agenttrace

# CLI
agenttrace --help        # ui（查看器）/ seed（造数）—— 骨架阶段，Sprint 1 起实现
```

Sprint 1 落地后的目标用法：

```python
import agenttrace

@agenttrace.trace()                 # 任务入口（G1：≤3 行接入）
def tutor(question): ...

with agenttrace.llm(model="qwen-plus") as gen:    # LLM 调用记录（流式自动聚合）
    ...

# 终端执行 `agenttrace ui` → 一条命令打开 Web 查看器回放
```

## 仓库结构

```
agenttrace/        SDK 包（api/intercept/context/model/pipeline/storage/mock/cli）
server/            FastAPI 只读查询 + 静态托管（agenttrace ui 启动）
web/               Vue 3 + Element Plus + ECharts 查看器
examples/          demo agent（课程答疑 + 笔记整理）与 mock 应答数据
tests/             pytest 单元 + 集成测试
docs/              SRS / M1 设计文档 / 项目计划 / 借鉴分析
models/            PlantUML 模型源文件（组件/类/时序/用例/功能结构）
.github/workflows/ CI（lint + test + coverage，ubuntu & windows 双平台）
```

## 开发约定

- 单人 Scrum + TDD：先测试后实现；**CI 全绿方可合并**（NFR-5）；
- 覆盖率门槛：Sprint 1 核心模块落地后启用 `--cov-fail-under=70`（TC-22）；
- SDK 保持零第三方依赖（openai 补丁为可选路径）；演示/测试不依赖真实 LLM API；
- 需求变更走 SRS 修订记录，需求-用例-测试追踪（附录 B）同步更新。

## 文档索引

- [需求规格说明书 SRS V0.5](docs/SRS初稿-AgentTrace.md)
- [M1 概要设计文档（AT-DES-001）](docs/M1设计文档-AgentTrace概要设计.md)
- [项目计划](docs/项目计划-Agent可观测性工具.md)
- [Langfuse 设计借鉴分析（AT-DES-000）](docs/设计研究-Langfuse借鉴分析.md)
- [需求调研记录](docs/需求调研-访谈提纲与痛点记录.md)
