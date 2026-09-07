# 交接文档：R9 后仓库一致性修复已发布

> 本文档用于跨会话交接。移动中的发布状态以远端 `feature/improvements` HEAD
> 及其对应 GitHub Actions 结果为准，避免把旧提交号误写成当前分支 HEAD。

## 一、当前状态

- **分支**：`feature/improvements`
- **R9 基线**：`91b55e5bf6ac776f3d42d9e46ec2763435d71708`
- **一致性修复提交**：`9160a6a5db9dd6c56366fff4c2bf1bd843268ca5`
- **CI 稳定性修复提交**：`adc76a9983e732803bd635be9e526b2553c2b953`
- **当前阶段**：R9 后的仓库一致性、工具契约与功能分支 CI 修复已完成
- **发布状态**：上述修复已进入 `feature/improvements` 发布序列；最终状态以分支 HEAD 和对应 CI 为准
- **项目边界**：单实例、本地优先；不内置多租户、RAG 或向量数据库

## 二、已发布能力（截至 R9）

- 计划式与 ReAct 调研执行、人工计划审批、恢复与重试。
- Run、Trace、Evidence、Citation、Report 的可追溯持久化与导出。
- 共享预算、子任务关联、失败原因保真和有界恢复。
- 信源治理、网页正文缓存、PDF 阅读、学术检索和 Skill 工作流。
- FastAPI、React/Vite 与 Streamlit 界面，以及 Docker 配置和本地持久化。
- R9 增加研究目标检查、数据口径约束、来源 ID 复用、重复抓取抑制和旧结果完整性标记。

本轮关键提交（文档提交本身以分支 HEAD 为准）：

```text
adc76a9 2026-09-07 test: stabilize feature branch CI gates
9160a6a 2026-09-07 fix: align repository contracts and file support
91b55e5 2026-09-07 fix: enforce research goals and bounded recovery
```

## 三、本轮已发布修复

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

本轮修复验证：

- Python `compileall` 通过。
- 定向修复与依赖契约 16 项通过；新增修复测试 9 项通过。
- 完整离线 pytest 收集 601 项：599 通过、2 条件跳过、0 失败、0 外部网络尝试；有 7 条第三方弃用警告。
- MCP server smoke、MCP client smoke、研究完整性 smoke 通过。
- 前端 OpenAPI 类型同步、类型检查、Lint、110 项测试、生产构建和 59 项 QA 检查通过。
- 首次功能分支 CI 确实触发，并暴露 OpenAPI 快照遗漏和 R4 测试环境泄漏；两项均已修复并完成本地等价门禁。
- MCP client 的旧固定数据已与 R9 质量门禁对齐：有来源正文的远端响应通过，无可用证据的 planned／ReAct 运行失败且保留失败 Trace。
- 未运行 Docker 实际构建／启动、真实浏览器或真实外部服务。

## 五、下一步与未验证项

- 发布前后的 `git diff --check`、`git status` 和文件范围均需保持干净；不得包含 `TASK.md`、`.env`、数据库或运行产物。
- 功能分支 CI 必须保持通过；出现失败时先处理失败门禁，再由部署环境拉取，当前不要自动修改部署目录。
- Docker 实际构建／启动、真实浏览器与真实外部模型／搜索仍需在具备条件的环境验收。
- `LICENSE` 按用户要求暂不处理。

## 六、工程注意事项

1. `TASK.md`、`docs/`、`.env`、`workspace/` 运行产物均为本地内容，不得提交。
2. 文件读取必须保持路径边界、逐文件 HITL 和长度／体积上限。
3. 外部系统写操作不得通过 MCP 暴露；本地持久化副作用必须在工具元数据中如实声明。
4. 所有工具执行继续保留 Trace；失败、拒绝和预算耗尽不得被报告生成覆盖。
5. 不要把固定数据或 mock 回归描述成真实联网验收。

(End of file)
