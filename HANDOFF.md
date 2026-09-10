# 交接文档：R11 Retrieval Foundation 本地验收完成、待发布

> 本文档用于跨会话交接。移动中的发布状态以远端 `feature/improvements` HEAD
> 及其对应 GitHub Actions 结果为准，避免把旧提交号误写成当前分支 HEAD。

## 一、当前状态

- **分支**：`feature/improvements`
- **当前已发布基线**：`b0edf9c4b1a121a18ec193b6977406c9d3f21738`
- **当前阶段**：`R11 — Retrieval & Source Reliability Foundation`
- **发布状态**：R10 已发布且 CI 成功；R11 实现和本地门禁已收口，尚未提交或推送
- **项目边界**：单实例、本地优先；不内置多租户、RAG 或向量数据库

## 二、已发布能力（截至 b0edf9c）

- 计划式与 ReAct 调研执行、人工计划审批、恢复与重试。
- Run、Trace、Evidence、Citation、Report 的可追溯持久化与导出。
- 共享预算、子任务关联、失败原因保真和有界恢复。
- 信源治理、网页正文缓存、PDF 阅读、学术检索和 Skill 工作流。
- FastAPI、React/Vite 与 Streamlit 界面，以及 Docker 配置和本地持久化。
- R9 增加研究目标检查、数据口径约束、来源 ID 复用、重复抓取抑制和旧结果完整性标记。
- R10 增加真实运行档位、OpenAI-compatible Provider、稳定模型错误分类、显式
  Runtime Preflight、宽松动态研究上限和最终报告软切换。

本轮关键提交（文档提交本身以分支 HEAD 为准）：

```text
b0edf9c 2026-09-09 feat(runtime): complete real runtime foundation
3040641 2026-09-08 fix: strengthen research recovery and simplify execution UI
de91b9d 2026-09-07 docs: finalize repository repair handoff
adc76a9 2026-09-07 test: stabilize feature branch CI gates
```

## 三、本轮 R11 待发布修复

当前修复范围不含 `LICENSE`：

1. 固定统一 FetchRequest／FetchResult 和稳定状态／失败分类。
2. 将 HTTP、HTML 提取、正文质量判断从 `web_fetcher` 拆分为可组合模块。
3. 新增隔离 Playwright Browser Backend，保留 SSRF、最终 URL、体积、下载和非持久 Profile 边界。
4. 新增 PDF 与 Firecrawl／Exa Adapter，并由 Adaptive Router 在单次工具调用内完成回退。
5. 增加 Canonical URL 与 Content Hash 两级去重及基础 Source Identity／转载归并元数据。
6. 将 Fetch 元数据接入 Evidence、SourceDocument、SourceSnapshot、Trace 与 Recovery。
7. 增加高级环境配置、固定 Playwright 依赖、Docker Chromium 与受确认保护的真实 R11 Smoke。
8. 保持 `web_fetcher` 外部名称和输出主结构兼容，不引入 R12 Coverage／Research Tree 或 R14 Long Report。

## 四、验证结果

- Python `compileall` 与 `git diff --check` 通过。
- 后端完整离线 pytest：收集 695 项，691 通过、2 条件跳过、2 严格 xfail、0 失败；7 条第三方弃用警告；0 次外部网络尝试。
- R11 专项及旧 Fetch／Evidence／Recovery 兼容回归通过。
- 前端本轮未修改；沿用 R10 的 104 项、类型检查、Lint 和生产构建通过基线。
- 综合项目 Smoke：18/18 通过；本地评估 78/80 通过，余下 2 项为明确标注的真实网络依赖，0 硬失败。
- Docker 静态配置 Smoke 通过；声明 Playwright Chromium 和 API 1 GB `/dev/shm`；当前环境没有 Docker CLI，未实际构建／启动。
- 未调用真实外网 Fetch／收费服务，未执行 Windows 浏览器人工验收。

## 五、下一步与未验证项

- 审阅当前差异后提交并推送到 `feature/improvements`，确认功能分支 CI 通过。
- 在 Windows 预览目录拉取新提交并重新构建 Docker；运行 `scripts/validate_real_runtime.py --confirm-real-calls --r11-fetch-smoke` 完成静态／Browser／PDF／已配置远端抓取验收。
- R11 发布后进入 R12 Deep Research Engine V2 替换；不在 R11 提前修复旧子 Run 聚合或长报告 Composer。
- `LICENSE` 按用户要求暂不处理。

## 六、工程注意事项

1. `TASK.md`、`docs/`、`.env`、`workspace/` 运行产物均为本地内容，不得提交。
2. 文件读取必须保持路径边界、逐文件 HITL 和长度／体积上限。
3. 外部系统写操作不得通过 MCP 暴露；本地持久化副作用必须在工具元数据中如实声明。
4. 所有工具执行继续保留 Trace；失败、拒绝和预算耗尽不得被报告生成覆盖。
5. 不要把固定数据或 mock 回归描述成真实联网验收。

(End of file)
