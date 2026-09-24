"""SQLite 存储层（agenttrace/storage，FR-3.1）。

职责（AT-DES-001 §3.2，DDL 定稿）：
- DDL 建表（traces/spans 两表 + 索引，PRAGMA WAL/synchronous=NORMAL）；
- 按 PRAGMA user_version 增量迁移（本期 v1）；
- 批量 UPSERT（占位 INSERT 与后续 UPDATE 合并落盘）；
- 线程安全的连接管理（flusher 线程独占写连接）。

Sprint 1 任务 3 按 TDD 实现（TC-09：重启后 trace 仍可查）。
"""
