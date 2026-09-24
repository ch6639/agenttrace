# M1 设计输入：Langfuse 设计借鉴分析

| 项目名称 | AgentTrace —— Agent 运行可观测性工具 |  |  |
|----------|--------------------------------------|------------------|------------|
| 文档编号 | AT-DES-000 | 版本 | V1.0 |
| 日期 | 2026-09-17 | 性质 | 设计研究（M1 概要设计的输入，不修改 SRS） |
| 研究对象 | Langfuse（https://langfuse.com/ ，https://github.com/langfuse/langfuse ）|  |  |

**一句话结论**：Langfuse 值得 AgentTrace 借鉴的不是它的功能清单，而是五个机制设计——观测类型语义、上下文栈自动嵌套、Span 先建后更新的写回模式、异步批量摄入管线、列表/时间线的信息架构；它的多组件存储架构与评估闭环（Score/Dataset/Experiment）应明确不抄，缩放为 SQLite 单机的等价物或推迟到展望。

---

## 1. 研究目的与方法

SRS 附录 A.1 已对 Langfuse 做过**产品层**竞品分析（结论：功能完整，但自托管面向团队，对单机场景过重）。本文深入到**设计层**：拆解其内部机制，逐条判断"采纳 / 适配缩小 / 放弃"，为 M1 概要设计（架构、模块划分、接口定稿、SQLite 表结构）提供决策依据。

资料来源（检索日期 2026-09-17）：

1. Langfuse 官网产品页（产品模块与定位）
2. 官方文档：Tracing / Data Model（https://langfuse.com/docs/observability/data-model ）
3. 官方文档：Python SDK Decorators（https://langfuse.com/docs/sdk/python/decorators ）
4. 官方文档：Scores Overview（https://langfuse.com/docs/scores/overview ）
5. 官方文档：Self-hosting 架构页（https://langfuse.com/self-hosting ）
6. GitHub 仓库 langfuse/langfuse（MIT 协议）

---

## 2. Langfuse 核心设计拆解

### 2.1 数据模型：Trace + Observation（三类型）+ Enrichment

- **Trace**：一次请求/任务的完整记录，是同一 `trace_id` 下所有观测的逻辑分组。
- **Observation**：trace 内的单个步骤，分三类：
  - **SPAN**：默认类型，有起止时间（时长有意义），用于工具调用、检索等操作；
  - **GENERATION**：LLM 调用专属，在 SPAN 基础上增加模型、token 用量（usageDetails）、成本（costDetails）、补丁开始时间等 LLM 字段；
  - **EVENT**：瞬时事件，无时长，用于"决策点"这类只标记发生时刻的记录。
- **嵌套**：Observation 通过 parent 引用组成树，反映应用的调用层级。
- **Enrichment 属性**：user_id、session_id（多轮会话分组）、tags、metadata（自由键值）、environment、版本——由 SDK 在上下文中自动传播到全部子观测。
- **存储模型**：所有 observation 存于**一张平铺宽表**，每行冗余 trace 级属性，避免查询时 join（ClickHouse OLAP 优化）。

### 2.2 存储：OLTP/OLAP 分离（多组件，重）

自托管 v4 架构：Web 容器（UI+API）+ Worker 容器 + **Postgres**（事务数据）+ **ClickHouse**（trace/observation/score 分析）+ **Redis**（队列与缓存）+ **S3/MinIO**（原始事件与大数据块）。面向 90B+ 观测/月的云上规模，OSS 与 Cloud 同码。

### 2.3 SDK：装饰器 + 上下文栈 + 异步缓冲

- `@observe()` 装饰器包装函数，自动捕获输入/输出/耗时/异常，无需改动函数内部逻辑；`as_type="generation"` 标记 LLM 调用。
- **调用层级即 trace 树**：被装饰函数调用另一个被装饰函数时，嵌套关系由 SDK 内部上下文自动维护（开发者零感知）。
- 装饰器、上下文管理器（`start_as_current_observation`）、手动 API 三种方式**互操作**，共享同一上下文。
- **异步缓冲**：事件先进 SDK 内存队列，后台线程批量上报；`flush()` 阻塞至队列处理完，`shutdown()` 再等待线程退出，`atexit` 自动兜底；短生命周期进程须显式 flush 防丢数据。
- IO 捕获可关闭（`capture_input/output=False`，含全局开关）——脱敏手段。
- 属性向上传播：内层函数可设置 user_id/session_id/tags，自动挂到整条 trace。

### 2.4 摄入管线：先落盘、再排队、异步消费

批量上报到达 Web 容器后：**立即写入 S3（先持久化）**→ 仅将引用指针放入 Redis 队列 → Worker 异步消费，从 S3 读回并写入 ClickHouse。可靠性保证：原始事件先落盘，即使数据库临时故障也不丢；数据库永远不成为摄入路径上的同步瓶颈。

### 2.5 评估闭环：Score + Dataset + Experiment

Score（数值/分类/布尔/文本四种）有三个来源：人工标注（UI 标注队列）、模型评估（LLM-as-a-Judge）、代码评估（确定性检查）；生产 trace 可转为 Dataset 测试集，Experiment 对比 prompt/模型/代码改动，评分可做发布门禁（CI 阈值拦截）。这是 Langfuse 的重头功能，构成"trace → 监控 → 数据集 → 实验 → 评估"闭环。

### 2.6 查看器信息架构

Trace 列表（按时间/用户/会话/标签/耗时/成本过滤）→ 时间线详情（嵌套瀑布式展示各观测的输入/输出/用量）→ 成本/时延/质量看板。时间线是核心画面。

---

## 3. 借鉴决策表

> 判定标准：是否服务于 SRS 已确认的范围（F1–F6、FR-1~FR-6）；是否与"单机、离线、轻量、零配置"定位冲突。**借鉴机制，不照搬规模。**

| # | Langfuse 机制 | 判定 | 对 AgentTrace 的落地方式 |
|---|--------------|------|--------------------------|
| 1 | Span/Generation/Event 三类型的**语义区分**（有时长操作 / LLM 专用字段 / 瞬时事件） | **采纳（语义）** | 保留 SRS 的 llm/tool/step 三类命名，吸收语义：llm/tool 有起止时间，step 为瞬时事件（无时长）。类图 `duration_ms` 对 step 可空 |
| 2 | Span 与异常信息分离（statusMessage 独立字段） | **采纳** | Span 增加 `status_message` 字段，异常不再塞进 output（FR-2.2 的"异常信息"有独立落点，UC-04 根因定位更清晰） |
| 3 | 装饰器上下文栈：调用层级自动成为 trace 树 | **采纳** | SDK 用 `contextvars` 维护当前 span 栈，parent_id 自动推导（线程/协程安全）。FR-2.3 的树形组织对开发者完全透明 |
| 4 | Span **先创建占位、结束/流式收尾时 update 写回** | **采纳** | 这是 FR-2.5（SSE 流结束时完成写回）的天然实现：span 入库时 status=running，结束时 UPDATE 聚合输出与累计 token |
| 5 | 异步缓冲 + `flush()`/`shutdown()` + atexit 兜底 | **采纳** | SDK 内存队列 + 后台批量写线程 + `agenttrace.flush()` 显式接口 + atexit 注册。直接支撑 FR-2.4 与 NFR-3 |
| 6 | IO 捕获开关（脱敏） | **适配** | 装饰器加 `capture_input/capture_output` 参数，配合 NFR-6（密钥不入 trace） |
| 7 | 摄入"先落盘再处理"的可靠性原则 | **适配（缩小）** | 单机等价物：SQLite `journal_mode=WAL` + 批量单事务写入 + 写失败内存重试→降级告警。不做二级存储（无 S3/Redis），但保留"数据先持久化、业务不等待"的原则 |
| 8 | 列表过滤维度（时间/状态/耗时/Token）与瀑布式时间线 | **适配** | FR-3.2 已定时间/状态过滤；**列表默认时间倒序 + 支持按耗时/Token 排序**属 API 设计自由度（不动 SRS）；FR-4.2 的时间线详情用瀑布图实现（开始/持续条 + 失败标红） |
| 9 | session_id 会话分组 | **预留（存储层）** | traces 表预留可空 `session_id` 列（零成本）；UI 与需求条目不做。若后续 demo 需要多轮演示再升级 |
| 10 | observation 平铺宽表 + trace 级属性冗余 | **不采纳** | 那是 ClickHouse 免 join 的 OLAP 优化；SQLite 擅长 join 且数据量小（NFR-2：1000 条 ≤2s 轻松达标），两表 + 索引即可，无需反范式 |
| 11 | Postgres+ClickHouse+Redis+S3 多组件架构 | **不采纳** | 与零部署定位根本冲突，正是 SRS A.1 选中该空白的原因 |
| 12 | Score 评估闭环（标注队列/LLM-as-Judge/Dataset/Experiment） | **不采纳（本期）** | 完整闭环是范围蔓延；FR-5.1 的"失败类型分布"已覆盖其最小价值。SRS 第 7 节展望已含"异常自动聚合"，其余不新增 |
| 13 | Prompt 管理 / Playground / 多用户协作 / OpenTelemetry 标准 | **不采纳** | 全部维持 SRS 非目标清单；OTel 兼容可作长线展望（如未来互操作需求） |

---

## 4. 对 M1 概要设计的具体建议

以下为 M1 评审草案，最终以 M1 设计文档定稿为准。

### 4.1 数据模型修订（对 `models/class-model.puml` 的增量）

Span 增加与调整（Trace 不变，另预留 session_id）：

- `status_message : str`（可空）——异常/状态说明，与 output 分离；
- `model : str`（可空）——llm 类型专用；
- `duration_ms` 可空——step 类型瞬时，无时长；
- `start_time/end_time` 取代隐式 created_at 语义（span 有起止；created_at 留作入库时间）。

### 4.2 SQLite 表结构草案

```sql
PRAGMA journal_mode = WAL;   -- 借鉴"先落盘"原则的单机等价物；写不阻塞读

CREATE TABLE traces (
  id           TEXT PRIMARY KEY,                 -- UUID
  name         TEXT NOT NULL,
  session_id   TEXT,                             -- 预留（借鉴决策 #9），可空
  status       TEXT NOT NULL DEFAULT 'running',  -- running/success/failed
  start_time   TEXT NOT NULL,                    -- ISO 8601 UTC
  end_time     TEXT,
  total_tokens INTEGER NOT NULL DEFAULT 0,
  latency_ms   INTEGER,
  error        TEXT,
  created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX idx_traces_start  ON traces(start_time DESC);
CREATE INDEX idx_traces_status ON traces(status);

CREATE TABLE spans (                             -- 有意不冗余 trace 级属性（决策 #10）
  id             TEXT PRIMARY KEY,
  trace_id       TEXT NOT NULL REFERENCES traces(id),
  parent_id      TEXT,                           -- 自关联，根 span 为空
  type           TEXT NOT NULL,                  -- llm / tool / step
  name           TEXT NOT NULL,
  input          TEXT,                           -- JSON 序列化
  output         TEXT,
  status         TEXT NOT NULL DEFAULT 'running',
  status_message TEXT,                           -- 异常信息独立落点（决策 #2）
  model          TEXT,                           -- llm 专用（决策 #1）
  token_in       INTEGER NOT NULL DEFAULT 0,
  token_out      INTEGER NOT NULL DEFAULT 0,
  start_time     TEXT NOT NULL,
  end_time       TEXT,
  duration_ms    INTEGER,                        -- step 为空（决策 #1）
  is_stream      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_spans_trace ON spans(trace_id);
CREATE INDEX idx_spans_parent ON spans(parent_id);
```

### 4.3 SDK 公开接口草案（修订 SRS 5.2 草案，M1 定稿）

```python
import agenttrace

@agenttrace.trace()                       # 任务根节点（≤3 行接入目标的主入口）
def tutor_task(question): ...

@agenttrace.tool(capture_output=False)    # 工具调用：函数名/参数/返回/异常自动记录
def search_notes(keyword): ...

with agenttrace.llm(model="qwen-plus") as gen:   # LLM 调用上下文（占位 span 先入库）
    ...                                            # 流式增量在内存聚合
    gen.update(output=text, token_in=..., token_out=...)  # 流结束写回（FR-2.5）

agenttrace.log_step("decide", {"next": "search_notes"})   # 瞬时决策事件
agenttrace.flush()                        # 显式排空队列；atexit 自动兜底（决策 #5）
```

要点：三类型三入口（trace/llm/tool + log_step），比 Langfuse 单一 `observe(as_type=...)` 更直白，贴合 ≤3 行接入目标；嵌套关系由 contextvars 上下文栈自动维护，开发者不传 parent_id。

### 4.4 写入可靠性设计（映射 FR-2.4 / NFR-3）

```
业务线程 ──span事件──▶ 内存队列（有界 deque + 锁；满则丢弃最旧并告警计数）
                          │
                          ▼
              后台 flusher 线程：≤500ms 或满 32 条触发，
              单事务批量 UPSERT 写 SQLite（WAL 模式）
                          │
              写失败 → 留在内存重试（≤3 次）→ 降级：
                      控制台告警 + 继续运行（旁路失效，NFR-3）
atexit / agenttrace.flush() / agenttrace.shutdown()：进程退出前排空队列
```

对 FR-2.4"1 秒内写入、异常退出最多丢最后一条"的解释：常态下批量间隔 ≤500ms 满足 1s；进程被强杀的极端情况下，丢失上限为队列中未落盘的最后一批（以压力测试标定，TC-07 验证口径据此细化）。

### 4.5 REST API 修订建议

在 SRS 5.2 草案（`GET /traces`、`GET /traces/{id}`、`GET /stats`）之上明确参数（属接口设计细节，不动 SRS 条目）：

- `GET /traces?status=&t_from=&t_to=&order=&limit=&offset=`，order 支持 `start_time|latency|tokens`（默认 start_time 倒序——决策 #8）；
- `GET /traces/{id}` 返回 trace + 展平的 span 列表（含 parent_id），前端组装瀑布树；
- `GET /stats` 返回总 Token、成功率、平均步数、失败类型分布（FR-5.1 四项）。

### 4.6 查看器设计要点（FR-4 实现形式）

- 列表页：时间倒序，列含时间/状态/步数/Token 总量/耗时，支持排序切换；
- 详情页：**瀑布式时间线**（横条表示各 span 起止与持续，按树形缩进），失败标红，点击展开完整输入/输出；流式事件展示聚合输出与累计 Token；
- 空态引导运行示例 Agent（UC-02 备选流 2a）。

---

## 5. 与 SRS 的关系

- 本文**不修改 SRS 任何需求条目**；第 4 节全部内容属于 M1 概要设计自由度（接口命名、表结构、实现形式）。
- 若评审后采纳 #9（session 预留）与 #12（评估推迟），无需动 SRS：前者只是存储层可空列，后者与第 7 节非目标一致。
- 后续若把"失败类型分布"细化为"失败根因标注"，须走 SRS 修订记录（V0.6+），并同步附录 B 追踪表。
- 本文可作为 SRS 附录 A.1 的深化材料在最终报告中引用；dogfooding（A.2）自 Sprint 1 起另行记录。
