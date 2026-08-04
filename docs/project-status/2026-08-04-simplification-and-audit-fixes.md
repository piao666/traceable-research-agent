# 2026-08-04 范围简化与审计修复说明

## 1. 修复目标

本轮改造将项目边界收敛为面向开源用户的单实例自托管研究 Agent。用户通过 Docker 部署，只需在项目根目录 `.env` 中配置 API Key 和运行参数。

本轮明确完成以下范围调整：

1. 取消多租户模型，不再从请求中解析或持久化 tenant/user 身份。
2. 删除项目内置 RAG、embedding、向量数据库和索引能力。
3. 删除 `task.txt`，将仓库根目录 `TASK.md` 设为唯一执行台账。
4. 完成前次代码审计确认的功能、迁移、成本追踪和外部检索修复。

## 2. 架构与运行方式调整

### 2.1 单实例自托管

- 删除多租户请求上下文、身份 Header、相关配置项和 ORM 隔离字段。
- 会话与长期记忆改为当前部署实例内的全局作用域。
- API 保留可选 `X-API-Key` 鉴权，用于限制自托管实例访问。
- 不提供用户注册、租户管理、配额或租户级数据隔离能力。

### 2.2 Docker 与环境变量

- Docker Compose 统一读取项目根目录 `.env`。
- `.env.example` 只保留当前运行路径实际使用的配置项。
- 删除单独的 Docker/RAG requirements 和真实 RAG Compose 覆盖文件。
- README 已更新为单实例 Docker 部署、API Key 配置和本地启动说明。
- 私有 `.env` 继续由 `.gitignore` 排除，未提交任何真实密钥。

## 3. 删除的 RAG 范围

项目不再假设不同用户采用相同的数据加载与检索方式，因此删除以下内置能力：

- `app/rag/` 下的 loader、chunker、embedding、BM25、hybrid search 和 vector store。
- `rag_search`、`rag_retrieval` 工具及 Tool Registry 注册项。
- RAG 索引构建、实验、smoke、eval 和对应测试。
- RAG 专用依赖、Docker 镜像层、Compose 配置和示例数据。
- Skill、示例文档、评测用例和运行配置中的 RAG 入口。

项目仍保留文件读取、只读 SQL、Web、学术检索、GitHub/MCP、报告生成和 trace 能力。部署者可根据自己的数据源，通过 Tool Registry 或 MCP 接入独立检索工具。

## 4. 审计问题修复

| 模块 | 修复内容 |
| --- | --- |
| Streamlit | 修复报告页在赋值前使用 `run_id` 的问题，并更新前端 smoke 的报告渲染断言。 |
| Alembic | 导入 memory models；新增 `0007_single_instance_memory.py` 删除 tenant/user 列；修复 `tool_traces.sub_query` 的 ORM/迁移索引漂移。 |
| 旧库升级 | legacy migration bootstrap 支持升级到 0007，避免已有 SQLite 数据库停留在旧 schema。 |
| Deepening | 修复子 run 创建时传入不存在参数，以及把 `AgentRun` 对象误作 run ID 的问题。 |
| LLM 成本 | planned reporter 将模型 usage 写入 `report_synthesis` trace，并纳入 run 级成本汇总。 |
| Tool Registry | 实际执行 `ToolSpec.timeout_seconds`，超时以结构化失败结果和 trace 呈现。 |
| arXiv | 修复查询双重编码、Atom category/primary category 解析，并加入 3 秒礼貌限流。 |
| Semantic Scholar | 结果链接改为有效的公开论文页面 URL。 |
| 依赖 | Python 依赖固定为明确版本，提高本地与容器构建的可复现性。 |

## 5. 数据库迁移影响

新增迁移：`migrations/versions/0007_single_instance_memory.py`。

升级行为：

- 从 `conversation_sessions` 删除 `tenant_id`、`user_id`。
- 从 `user_memories` 删除 `tenant_id`、`user_id`。
- 删除对应的多租户联合索引。

降级迁移会恢复上述列和索引，仅用于 schema 回滚。升级前仍建议备份已有 SQLite 数据库。

## 6. 验证结果

| 检查项 | 结果 |
| --- | --- |
| `python -m compileall -q app scripts frontend migrations tests` | 通过 |
| `python -m pytest -q` | 195 passed，15 subtests；仅 `.pytest_cache` 权限警告 |
| `python scripts/smoke_final_project.py` | 18/18 通过 |
| `python -m app.eval.run_eval` | 12/12 通过 |
| FastAPI 真实 HTTP 闭环 | `/health` 正常；任务完成；生成 2 条 trace 和 Markdown 报告 |
| Streamlit HTTP 启动 | HTTP 200，页面 HTML 正常 |
| `docker compose config --quiet` | 通过；仅 Docker 用户配置文件权限警告 |
| `git diff --check` | 通过；仅 Windows CRLF 转换提示 |

## 7. 已知限制

当前验证环境无权访问 `npipe:////./pipe/docker_engine`，因此未能实际执行 Docker 镜像构建和容器启动。Compose 配置已经过静态校验，Python 本地运行、API 闭环和 Streamlit 启动均已验证。

本项目不再提供通用 RAG 实现。需要私有知识检索的部署者应按自己的数据权限、索引策略和检索协议接入自定义只读工具。

## 8. 提交记录

- `74d999b`：单实例自托管改造、删除 RAG 和审计修复主体。
- `3929fce`：回填 `TASK.md` checkpoint 状态。
- 本说明文档：待提交并推送到 `origin/feature/improvements`。
