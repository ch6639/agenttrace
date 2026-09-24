"""数据模型（agenttrace/model，FR-2.x）。

职责（AT-DES-001 §2.2/§3）：
- Trace / Span 数据类（字段与 models/class-model.puml 定稿一致）；
- input/output JSON 序列化（不可序列化对象转 repr）；
- 单字段超 64KiB 截断并置 __truncated__ 标记（D-08）；
- 业务时间（start/end）与入库时间（created_at）分离；duration 用 monotonic 计算。

Sprint 1 任务 1 按 TDD 实现。
"""
