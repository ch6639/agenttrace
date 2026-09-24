"""离线 Mock 回放（agenttrace/mock，FR-1.3/FR-6.2）。

职责（AT-DES-001 §5.4）：
- AGENTTRACE_MOCK=1 启用；llm() 返回 examples/mock/responses.jsonl 预存应答
  （按 model 维度轮次顺序取用），照常产生真实形状的 span 与 token 统计；
- 演示与测试全程无网络、无密钥（演示不赌网络）。

Sprint 1 任务 6 按 TDD 实现。
"""
