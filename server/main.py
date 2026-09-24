"""FastAPI 查询服务（server）。

职责（AT-DES-001 §2.2/§4.2）：
- 只读查询 /api/traces（过滤/排序/分页）、/api/traces/{id}（trace+展平 spans）、
  /api/stats（FR-5.1 四项统计）、/api/health；
- 托管 web 构建产物，由 `agenttrace ui` 以 uvicorn 启动（单进程单端口）。

依赖不属于 SDK 零依赖核心，经 `pip install -e ".[server]"` 安装。
Sprint 1 垂直切片实现 /api/traces 只读版；M3 完成全部端点与静态托管。
"""
