# AgentTrace 概要设计说明书（M1）

| 项目名称 | AgentTrace —— Agent 运行可观测性工具 |  |  |
|----------|--------------------------------------|------------------|------------|
| 文档编号 | AT-DES-001 | 版本 | V1.0 |
| 作者 | 【待填：学号-姓名】 | 指导教师 | 【待填】 |
| 日期 | 2026-09-17 | 状态 | M1 定稿（4 项设计决策已确认；交付时转 Word 版） |

**修订历史**

| 版本 | 日期 | 修订内容 | 修订人 |
|------|------|----------|--------|
| V1.0 | 2026-09-17 | 初版定稿：总体架构、数据设计、SDK/REST/CLI 接口、关键机制、UI 概要、设计决策记录（D-01~D-10）、Sprint 1 任务分解 | 【待填】 |

---

## 1 引言

### 1.1 编写目的

本文档定义 AgentTrace 的总体架构、模块划分、数据结构、接口与关键机制设计，作为 Sprint 1/2 编码实现与 M4 测试的依据。上游输入：SRS V0.5（AT-SRS-001，需求基线）、《Langfuse 设计借鉴分析》（AT-DES-000，机制借鉴决策表）。

### 1.2 设计范围

覆盖 SRS 全部功能块 F1~F6 与非功能需求 NFR-1~NFR-6 的设计侧。**不修改 SRS 任何需求条目**；本文档中的接口命名即 SRS 5.2 遗留待办"接口命名 M1 定稿"的定稿结果。

### 1.3 已确认的设计决策（2026-09-17，用户拍板）

| 编号 | 决策 | 对 SRS 的影响 |
|------|------|----------------|
| D-01 | LLM 拦截：**显式 `with agenttrace.llm()` 为主 + 可选 `agenttrace.patch_openai()` 自动补丁**（检测到 openai 包才生效，未装则静默跳过） | G1/FR-1.1 口径明确为"任务入口处 ≤3 行"（见 §4.1） |
| D-03 | 时间线详情页：**瀑布图 + 选中详情**双区布局 | FR-4.2/4.3 的实现形式 |
| D-04 | 前端：**Element Plus**（组件）+ ECharts（统计图表）；瀑布条自绘（CSS/SVG） | 不改需求；组件选型落定 |
| D-10 | **垂直切片纳入 Sprint 1 尾部**：造数 CLI + 只读 API + 最简列表页 | 不改 SRS（UI 本在范围内，仅提前）；造数脚本兼作 TC-19 数据源 |

其余决策 D-02、D-05~D-09 见第 7 章设计决策记录。

---

## 2 总体架构

### 2.1 架构总览

三层单机架构：**SDK（被测进程内）→ SQLite（单文件）→ 查看器（server + web）**。组件图源文件：`models/component-architecture.puml`。

```
┌────────────────────────────────────────────────────────────┐
│ 被测 Agent 进程（Python 3.11+）                              │
│  ┌─────────────── agenttrace SDK（零第三方依赖）──────────┐ │
│  │ api        公开接口与初始化（trace/tool/llm/flush…）    │ │
│  │ intercept  拦截器（装饰器/LLM上下文/openai补丁）        │ │
│  │ context    上下文栈（contextvars，parent 自动推导）     │ │
│  │ model      数据模型（Trace/Span/序列化/截断）           │ │
│  │ pipeline   摄入管线（内存队列 + flusher 后台线程）      │ │
│  │ storage    SQLite 写入（WAL/批量事务/UPSERT）           │ │
│  │ mock       离线回放（FR-1.3/6.2）                       │ │
│  └───────────────────────────────────────────────────────│ │
└──────────────────────┬─────────────────────────────────────┘
                       ▼
              SQLite（agenttrace.db，WAL 模式）
                       ▲ 只读 SELECT
┌──────────────────────┴─────────────────────────────────────┐
│ 查看器（一条命令 agenttrace ui）                             │
│  server：FastAPI（/api/traces /api/traces/{id} /api/stats   │
│          + 前端静态文件托管，单进程单端口）                   │
│  web：Vue 3 + Element Plus + ECharts（列表/时间线/统计）     │
└────────────────────────────────────────────────────────────┘
```

### 2.2 模块职责与需求映射

| 模块 | 职责 | 关键设计点 | 对应需求 |
|------|------|-----------|----------|
| `agenttrace/api` | 公开接口、全局配置、atexit 注册 | 默认零配置（库位置见 D-09） | FR-1.1 |
| `agenttrace/intercept` | 装饰器（trace/tool）、llm() 上下文、patch_openai | 异常透传并记录；IO 捕获开关 | FR-1.2、FR-2.1、FR-2.2 |
| `agenttrace/context` | contextvars 维护当前 span 栈 | 嵌套自动推导 parent_id，开发者零感知 | FR-2.3 |
| `agenttrace/model` | Trace/Span 数据类、JSON 序列化、大对象截断（D-08） | 输入快照在入队前完成 | FR-2.x |
| `agenttrace/pipeline` | 有界内存队列、flusher 线程、重试与降级、flush/shutdown | 业务线程只做"入队即返回" | FR-2.4、NFR-1、NFR-3 |
| `agenttrace/storage` | DDL/迁移、批量 UPSERT、连接管理 | WAL + synchronous=NORMAL | FR-3.1 |
| `agenttrace/mock` | 预存应答回放，代替真实 LLM API | 环境变量 `AGENTTRACE_MOCK=1` 启用 | FR-1.3、FR-6.2 |
| `agenttrace/cli` | `ui` / `seed` 子命令 | 一条命令启动查看器；造数兼作 TC-19 数据源 | NFR-4、TC-19 |
| `server/` | FastAPI 只读查询 + 静态托管 | OpenAPI 文档自动生成（FastAPI 自带） | FR-3.2、FR-4.x、FR-5.1 |
| `web/` | 三视图单页应用 | 瀑布条自绘；图表用 ECharts | FR-4.x、FR-5.1 |

### 2.3 运行形态

1. **记录态**：开发者在自己的 Agent 进程中 import SDK，数据持续追加至 SQLite。读写同库时由 WAL 保证"写不阻塞读"。
2. **查看态**：`agenttrace ui`（内部启动 uvicorn，FastAPI 同时服务 API 与前端静态文件，单进程单端口，默认 http://127.0.0.1:8320）。
3. **离线演示态**：`AGENTTRACE_MOCK=1` 运行示例 Agent，全程无网络、无密钥。

### 2.4 架构设计原则（源自 AT-DES-000）

1. **旁路失效**（NFR-3）：观测链路任何环节故障只降级、不阻断业务；
2. **异步摄入**（借鉴 Langfuse #5/#7）：业务线程只入队，落盘由后台线程批量完成；
3. **零依赖核心**：SDK 仅用标准库；openai 补丁为可选路径（D-01）；
4. **数据先持久化**：SQLite WAL 即"先落盘再处理"的单机等价物。

---

## 3 数据设计

### 3.1 概念模型

类图源文件：`models/class-model.puml`（M1 定稿版）。相对 SRS 5.1 草案的变更：

| 变更 | 理由 | 来源 |
|------|------|------|
| Trace 新增 `session_id`（可空） | 会话分组预留，零成本 | AT-DES-000 决策 #9 |
| Span 新增 `status_message` | 异常信息与 output 分离，UC-04 根因定位更清晰 | 决策 #2 |
| Span 新增 `model`（llm 专用） | 区分观测类型语义 | 决策 #1 |
| `duration_ms` 对 step 类型可空 | step 为瞬时事件，无时长 | 决策 #1 |
| 区分 `start/end_time`（业务时间）与 `created_at`（入库时间） | 支撑时序分析与延迟核算 | 设计需要 |

### 3.2 SQLite 表结构（DDL 定稿）

```sql
PRAGMA journal_mode = WAL;      -- "先落盘"原则的单机等价物；写不阻塞读
PRAGMA synchronous  = NORMAL;   -- WAL 下安全且快，配合批量事务
PRAGMA user_version = 1;        -- 模式版本；启动时按版本增量迁移（本期仅 v1）

CREATE TABLE traces (
  id           TEXT PRIMARY KEY,                 -- UUID4
  name         TEXT NOT NULL,
  session_id   TEXT,                             -- 预留（D 决策 #9），可空
  status       TEXT NOT NULL DEFAULT 'running',  -- running/success/failed
  start_time   TEXT NOT NULL,                    -- ISO 8601 UTC（毫秒精度）
  end_time     TEXT,
  total_tokens INTEGER NOT NULL DEFAULT 0,
  latency_ms   INTEGER,
  error        TEXT,
  created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX idx_traces_start  ON traces(start_time DESC);
CREATE INDEX idx_traces_status ON traces(status);

CREATE TABLE spans (
  id             TEXT PRIMARY KEY,
  trace_id       TEXT NOT NULL REFERENCES traces(id),
  parent_id      TEXT,                           -- 根 span 为空；自关联组成树
  type           TEXT NOT NULL,                  -- llm/tool/step
  name           TEXT NOT NULL,
  input          TEXT,                           -- JSON 序列化；超 64KiB 截断（D-08）
  output         TEXT,
  status         TEXT NOT NULL DEFAULT 'running',
  status_message TEXT,                           -- 异常/状态说明，独立落点
  model          TEXT,                           -- 仅 llm
  token_in       INTEGER NOT NULL DEFAULT 0,
  token_out      INTEGER NOT NULL DEFAULT 0,
  start_time     TEXT NOT NULL,
  end_time       TEXT,
  duration_ms    INTEGER,                        -- step 为空
  is_stream      INTEGER NOT NULL DEFAULT 0,
  created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX idx_spans_trace  ON spans(trace_id);
CREATE INDEX idx_spans_parent ON spans(parent_id);
```

**有意不做** observation 宽表冗余（AT-DES-000 决策 #10）：那是 ClickHouse 免 join 的 OLAP 优化；SQLite 擅长 join 且数据量小，两表 + 索引即可满足 NFR-2。

### 3.3 数据字典与约定

| 项 | 约定 |
|----|------|
| ID | UUID4 字符串 |
| 时间 | ISO 8601 UTC，毫秒精度；duration 用 `time.monotonic_ns()` 差值计算，不受系统改时影响 |
| input/output | JSON 序列化（str/dict/list 直接存；不可序列化对象转 `repr()`）；**单字段超 64KiB 截断并置 `"__truncated__": true`**（D-08，防库膨胀） |
| token 来源优先级 | ① 响应自带 usage（真实值）→ ② `len(text)//4` 近似并标记 `"__estimated__": true`（D-08） |
| 密钥安全 | 拦截层永不捕获请求头/auth 参数；`capture_input=False` 可整体关闭（NFR-6） |

---

## 4 接口设计（定稿）

### 4.1 SDK 公开接口 v1.0

```python
import agenttrace

# —— 任务入口（G1 口径：此处 ≤3 行）——
@agenttrace.trace()                        # ① 根上下文 + Trace 占位
def tutor(question): ...                   #    （可选 ② agenttrace.patch_openai()）

# —— 记录点（逐点一行，不改函数体业务逻辑）——
@agenttrace.tool(capture_output=True)      # 工具调用：函数名/参数/返回/异常自动记录
def search_notes(keyword): ...

with agenttrace.llm(model="qwen-plus") as gen:     # LLM 调用（显式，通用任何调用方式）
    resp = call_llm(...)
    gen.update(output=resp.text, token_in=210, token_out=512)   # 流式收尾一次写回

agenttrace.log_step("decide", {"next": "search_notes"})          # 瞬时决策事件

# —— 生命周期 ——
agenttrace.init(db_path="./agenttrace.db", enabled=True)   # 可选；不调用则用默认值（D-09）
agenttrace.flush(timeout=5)         # 排空队列（阻塞至落盘）
agenttrace.shutdown()               # flush + 停止 flusher；atexit 已自动注册
agenttrace.patch_openai()           # 可选自动补丁：未安装 openai 包则静默跳过（D-01）
```

**接口语义要点**：

1. **G1/FR-1.1 口径定稿**：≤3 行指"任务入口接入行数"（`import` + `@trace` 装饰器 = 2 行；启用自动补丁则 +1 行 = 3 行）。各记录点为单行装饰器或 with 包裹。TC-01 按此口径核对 demo Agent 入口接入区块。
2. **FR-1.2/TC-02 口径**：接入前后业务逻辑 diff 为零——装饰器只出现在函数定义行；demo Agent 的 LLM 调用统一收敛到内部 `ask_llm()` 助手，`with llm()` 只包裹该助手内一处调用语句，语句本身不变（D-02）。
3. **异常语义**：被装饰函数/with 块内异常照常向 Agent 抛出（业务行为零改变），同时记录 `status=failed` + `status_message`（异常类型与栈摘要）。
4. **`gen.update()`**：`output / token_in / token_out / model / error / status_message` 可选参数；流式场景下 with 块内部逐 chunk 只做内存聚合，退出时一次 update（§5.3）。
5. **patch_openai**：monkey-patch openai≥1.x 的 `chat.completions.create`（含 `stream=True`）；兼容性检测失败 → 发出 warning 并 no-op；补丁开关 `AGENTTRACE_NO_PATCH=1` 可禁用。

### 4.2 REST API 定稿（前缀 `/api`，OpenAPI 文档由 FastAPI 自动生成于 `/docs`）

| 端点 | 方法 | 参数 | 返回 |
|------|------|------|------|
| `/api/traces` | GET | `status`（success/failed）、`t_from`、`t_to`（ISO 8601）、`order`（start_time\|latency\|tokens，默认 start_time）、`dir`（asc/desc，默认 desc）、`limit`（默认 50）、`offset` | `{total, items:[{id,name,status,start_time,latency_ms,total_tokens,span_count}]}` |
| `/api/traces/{id}` | GET | — | `{trace:{…}, spans:[…展平，含 parent_id…]}`，前端组树 |
| `/api/stats` | GET | `t_from`、`t_to`（可选时间窗） | `{total_traces, success_rate, total_tokens, avg_steps, failure_types:{type→count}}` |
| `/api/health` | GET | — | `{ok:true, db:path, version:…}` |

### 4.3 CLI 定稿

| 命令 | 功能 |
|------|------|
| `agenttrace ui [--db PATH] [--host] [--port]` | 一条命令启动查看器（NFR-4）；默认读 `./agenttrace.db` |
| `agenttrace seed [--n 1000] [--db PATH] [--seed 42]` | 造数：生成嵌套 span 的合成 trace（固定随机种子可复现）；`--n 1000` 即 TC-19 性能测试数据源（D-10） |

---

## 5 关键机制设计

### 5.1 摄入管线（FR-2.4 / NFR-1 / NFR-3）

时序图：`models/sequence-ingestion.puml`。

| 参数 | 值 | 说明 |
|------|-----|------|
| 队列容量 | 1024 条（有界） | 满时丢弃最旧 + 计数告警（旁路失效） |
| 批量触发 | 32 条 或 500ms | 满足 FR-2.4"1 秒内写入" |
| 写入方式 | 单事务批量 UPSERT | 占位 INSERT 与后续 UPDATE 合并落盘 |
| 写失败 | 重试 ≤3 次 → 跳过该批 + 控制台告警 | 绝不抛入业务线程（NFR-3） |
| 退出兜底 | atexit 自动 + 显式 flush(timeout=5s) | 丢失上限 = 强杀时队列中未落盘的最后一批 |

**FR-2.4 验收口径**（TC-07）：常态批量间隔 ≤500ms 满足"1 秒内写入"；"最多丢最后一条"按"最多丢最后一批（≤32 条）"从严解释并以故障注入测试标定——若评审认为必须逐条落盘，则改为 span 占位即时单条 INSERT + 后台聚合 UPDATE（性能略降，M4 对比后定）。

### 5.2 上下文栈与嵌套（FR-2.3）

- `contextvars.ContextVar` 维护"当前 span 栈"，`@trace`/`@tool`/`with llm()` 进入即 push、退出即 pop，parent_id 自动取栈顶；
- 协程安全：asyncio task 继承上下文副本，嵌套关系正确；
- **已知限制（写入文档）**：业务代码把工具调用提交到线程池/其他线程时，ContextVar 不随线程传播，该 span 将挂到根节点。本期不处理（SRS 非目标：分布式/多进程追踪）；demo Agent 不使用线程池，不受影响。

### 5.3 流式写回（FR-2.5）

时序图：`models/sequence-streaming.puml`。占位 span 先入库（详情页立即可见"进行中"）→ chunk 逐块**内存聚合**（避免逐块写库的写入放大）→ with 退出时一次 update 写回完整输出与累计 token；流中断则保留部分聚合 + 中断原因；进程强杀则占位 span 停留 running，查看器将"running 且超时未更新"的流式 span 标注"疑似中断"（渲染层兜底，不改数据）。

### 5.4 Mock 模式（FR-1.3 / FR-6.2）

`AGENTTRACE_MOCK=1` 时，`llm()` 返回 `examples/mock/responses.jsonl` 中预存应答（按 model 维度轮次顺序取用），照常产生真实形状的 span 与 token 统计；demo Agent 双工具全流程离线可跑。

### 5.5 性能预算（NFR-1，M4 标定）

单事件开销 = 对象构造 + JSON 序列化 + 入队（微秒级）；落盘由批量事务摊薄（毫秒级/批，异步不占业务线程）。预估总开销 <2%，M4 以 TC-18 实测定稿阈值。

---

## 6 用户界面设计概要（web/）

技术栈：Vue 3 + Vite + **Element Plus**（表格/标签/空态/分页/布局）+ **ECharts**（统计页图表）；瀑布条为轻量自绘（CSS 定位横条，属布局元素而非图表，不违反"不自研图表"非目标）（D-03/D-04）。

| 视图 | 路由 | 内容 |
|------|------|------|
| 列表页 | `/` | el-table：时间、名称、状态 tag、步数、Token、耗时；过滤（状态/时间范围）+ 排序切换（时间/耗时/Token）+ 分页；空态引导运行示例 Agent |
| 时间线详情 | `/traces/:id` | 上半区**瀑布图**（树形缩进横条，横轴时间，失败标红，流式/进行中有标识）；下半区**选中详情**：完整 input/output（JSON 折叠展示）、token、耗时、status_message、流式聚合标记 |
| 统计总览 | `/stats` | 四指标卡（总 Token、成功率、平均步数、trace 总数）+ 失败类型分布（饼图）+ 每日 Token/成功率趋势（折线，ECharts） |

**Sprint 1 垂直切片范围（D-10）**：`agenttrace seed` + `GET /api/traces` + 列表页只读版（无过滤排序亦可）。时间线详情页与统计页仍按里程碑在 M3 完成。

---

## 7 设计决策记录（ADR 汇总）

| 编号 | 决策 | 背景/备选 | 理由 |
|------|------|-----------|------|
| D-01 | 显式拦截为主 + 可选 patch_openai | 纯显式（最稳）/ 自动补丁优先（体验最好） | 显式路径零依赖、易测试、通用任何 LLM 调用方式；补丁仅作演示增强，未装 openai 静默跳过。用户确认（2026-09-17） |
| D-02 | demo Agent LLM 调用收敛到 `ask_llm()` 单点包裹 | 各调用点分别包裹 | 严格满足 TC-02"业务逻辑零改动"口径 |
| D-03 | 时间线 = 瀑布图 + 选中详情双区 | 纯瀑布（浮层）/ 垂直列表 | 兼顾直观性与信息完整；用户确认 |
| D-04 | Element Plus + ECharts；瀑布条自绘 | Naive UI / 手写样式 | 组件开箱即用、中文文档全；甘特条属布局元素自绘最可控。用户确认 |
| D-05 | SQLite 两表 + 索引，**不做**宽表冗余 | Langfuse 式 observation 宽表 | 宽表是 ClickHouse 免 join 的 OLAP 优化；SQLite 无此痛点（AT-DES-000 #10） |
| D-06 | 摄入 = 有界内存队列 + 批量 UPSERT + atexit | 同步逐条写 | 借鉴 Langfuse 异步批量摄入；开销与可靠性兼顾（AT-DES-000 #5/#7） |
| D-07 | 评估闭环（Score/Dataset/Experiment）不纳入 | — | 范围蔓延；FR-5.1 失败类型分布已覆盖最小价值（AT-DES-000 #12），维持 SRS 非目标 |
| D-08 | input/output 64KiB 截断；token 缺失时 len//4 近似并标记 | 无限制/引入分词器精确计数 | 防库膨胀、守零依赖；估算透明标记，M4 可对比误差 |
| D-09 | 默认库位置 `./agenttrace.db`，`init()`/CLI 可覆盖 | 用户主目录 | 当前目录直观可见、demo 友好；`agenttrace ui --db` 指定 |
| D-10 | 垂直切片纳入 Sprint 1 尾部 | 只做造数脚本 / 严格按里程碑 | 10 月中即可见 UI、提前暴露前端风险；造数兼作 TC-19 数据源。用户确认，不改 SRS |

---

## 8 设计-需求追踪

| 需求 | 设计落点（本文章节 / 模块） | 模型图 |
|------|---------------------------|--------|
| FR-1.1/1.2 | §4.1 接口语义 1/2（G1 与 TC-02 口径） | — |
| FR-1.3、FR-6.2 | §5.4 Mock 模式（`agenttrace/mock`） | — |
| FR-2.1/2.2 | §2.2 intercept；§3.3 数据字典 | 类图 |
| FR-2.3 | §5.2 上下文栈（`agenttrace/context`） | 类图 |
| FR-2.4 | §5.1 摄入管线（`agenttrace/pipeline`） | 时序-摄入 |
| FR-2.5 | §5.3 流式写回 | 时序-流式 |
| FR-3.1/3.2 | §3.2 DDL；§4.2 REST 过滤参数 | 组件图 |
| FR-4.1~4.4 | §6 列表页/详情页；§4.2 API | 时序-查询 |
| FR-5.1 | §6 统计页；`/api/stats` | 时序-查询 |
| FR-6.1 | §2.2 demo Agent（`examples/`，D-02） | 组件图 |
| NFR-1 | §5.5 性能预算（M4 标定） | — |
| NFR-2 | §3.2 索引设计 + D-05 | — |
| NFR-3 | §5.1 降级策略 | 时序-摄入 |
| NFR-4 | §2.3 运行形态；§4.3 CLI | 组件图 |
| NFR-5 | §2.2 模块划分（可测性：pipeline/storage 纯函数化） | — |
| NFR-6 | §3.3 密钥安全；§4.1 capture 开关 | — |

---

## 9 Sprint 1（M2）任务分解（含 D-10 垂直切片）

TDD 顺序（每个任务先写测试）：

1. `model`：Trace/Span 数据类、序列化、截断（D-08）；
2. `context`：上下文栈 push/pop、parent 推导（含 asyncio 用例）；
3. `storage`：DDL/迁移、批量 UPSERT、重启可查（TC-09）；
4. `pipeline`：队列、flusher、重试降级、flush/shutdown/atexit（TC-07/TC-20）；
5. `intercept`：`@trace`/`@tool`/`llm()`/`log_step` + 异常记录（TC-04~TC-06、TC-08）；
6. `mock` + demo Agent 双工具（TC-03、TC-16/17）；
7. **垂直切片**：`agenttrace seed`（TC-19 数据源）→ `server` 只读 `/api/traces` → `web` 列表页只读版；
8. 全程同步记录 SRS 附录 A.2 dogfooding（每遇到观测痛点记一条）。

---

## 附录 PlantUML 模型文件清单

| 文件 | 内容 | 状态 |
|------|------|------|
| `models/component-architecture.puml` | 总体架构组件图 | M1 新增 |
| `models/class-model.puml` | 概念数据模型类图 | M1 修订定稿 |
| `models/sequence-ingestion.puml` | UC-01 摄入管线时序 | M1 新增 |
| `models/sequence-streaming.puml` | FR-2.5 流式写回时序 | M1 新增 |
| `models/sequence-query.puml` | UC-02/03/04 查询回放时序 | M1 新增 |
| `models/function-structure.puml` | 功能结构图 | 沿用 M0 |
| `models/use-case.puml` | 用例图 | 沿用 M0 |
