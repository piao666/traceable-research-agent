# 交接文档：R9 后仓库一致性修复已完成

> 本文档用于跨会话交接。它以远端已发布版本为基线，并把尚未提交的修复与已发布内容明确分开。

## 一、当前状态

- **分支**：`feature/improvements`
- **已发布基线**：`91b55e5bf6ac776f3d42d9e46ec2763435d71708`
- **当前阶段**：R9 后的仓库一致性与工具契约修复已完成
- **发布状态**：R9 基线已推送；本轮修复以 `feature/improvements` 最新提交和对应 CI 为准
- **项目边界**：单实例、本地优先；不内置多租户、RAG 或向量数据库

## 二、已发布能力（截至 R9）

- 计划式与 ReAct 调研执行、人工计划审批、恢复与重试。
- Run、Trace、Evidence、Citation、Report 的可追溯持久化与导出。
- 共享预算、子任务关联、失败原因保真和有界恢复。
- 信源治理、网页正文缓存、PDF 阅读、学术检索和 Skill 工作流。
- FastAPI、React/Vite 与 Streamlit 界面，以及 Docker 配置和本地持久化。
- R9 增加研究目标检查、数据口径约束、来源 ID 复用、重复抓取抑制和旧结果完整性标记。

最近三次已发布提交：

```text
91b55e5 2026-09-07 fix: enforce research goals and bounded recovery
6858dda 2026-09-03 fix: bound research recovery and preserve traceable evidence
5e188cd 2026-09-02 fix: complete trustworthy research workflows and frontend modules
```

## 三、本轮本地修复

当前修复范围不含 `LICENSE`：

1. 更新本交接文档、发布验证状态和 README 中混用的旧验证数字。
2. 让 `feature/improvements` 的直接 push 触发 CI。
3. 修正 MCP `skill_runner` 契约：外部信源只读，但会创建本地 Run、Trace、Evidence 和报告，因此对客户端声明为非只读、非无副作用。
4. 将 MCP `skill_runner.parameters` 传入 Skill 计划，校验未知参数和基础类型。
5. 更新 `sql_query` 描述，使其与现有 `sqlglot` 只读校验一致。
6. 为 `file_reader` 增加受限的 DOCX、XLSX 只读解析；PDF 继续由 `pdf_reader` 负责。
7. 增加对应回归测试和 API 运行依赖 `openpyxl`。

## 四、验证结果

R9 已发布验证记录：

- R9 专项 34 项通过。
- 后端 unittest 发现 582 项，其中 570 项通过，12 项因缺 pytest／Streamlit 导入失败；零断言失败、零外部网络尝试，不等于完整 pytest 通过。
- 前端 110 项测试、类型检查、Lint 和生产构建通过。
- 隔离页面／路由检查 59 项通过。

本轮本地修复验证：

- Python `compileall` 通过。
- 定向修复与依赖契约 16 项通过；新增修复测试 9 项通过。
- 完整离线 pytest 收集 601 项：599 通过、2 条件跳过、0 失败、0 外部网络尝试；有 7 条第三方弃用警告。
- MCP server smoke、MCP client smoke、研究完整性 smoke 通过。
- MCP client 的旧固定数据已与 R9 质量门禁对齐：有来源正文的远端响应通过，无可用证据的 planned／ReAct 运行失败且保留失败 Trace。
- 未运行 Docker 实际构建／启动、前端测试、真实浏览器或真实外部服务。本轮没有前端源码改动；前端 110 项是 R9 已发布基线，不是本轮重跑结果。

## 五、下一步与未验证项

- 最终 `git diff --check`、`git status` 和待提交文件核对已通过；实际提交前应再次确认不包含 `TASK.md`、`.env`、数据库或运行产物。
- 观察新启用的功能分支 CI；CI 通过后再由部署环境拉取，当前不要自动修改部署目录。
- Docker 实际构建／启动、真实浏览器与真实外部模型／搜索仍需在具备条件的环境验收。
- `LICENSE` 按用户要求暂不处理。

## 六、工程注意事项

1. `TASK.md`、`docs/`、`.env`、`workspace/` 运行产物均为本地内容，不得提交。
2. 文件读取必须保持路径边界、逐文件 HITL 和长度／体积上限。
3. 外部系统写操作不得通过 MCP 暴露；本地持久化副作用必须在工具元数据中如实声明。
4. 所有工具执行继续保留 Trace；失败、拒绝和预算耗尽不得被报告生成覆盖。
5. 不要把固定数据或 mock 回归描述成真实联网验收。

(End of file)
