"""摄入管线（agenttrace/pipeline，FR-2.4/NFR-1/NFR-3）。

职责（AT-DES-001 §5.1，时序图 models/sequence-ingestion.puml）：
- 有界内存队列（容量 1024）：满则丢弃最旧 + 计数告警（旁路失效）；
- flusher 守护线程：32 条或 500ms 触发批量单事务 UPSERT；
- 写失败重试 ≤3 次 → 跳过该批 + 控制台告警，绝不抛入业务线程；
- flush(timeout)/shutdown() + atexit 兜底；业务线程只"入队即返回"。

Sprint 1 任务 4 按 TDD 实现。
"""
