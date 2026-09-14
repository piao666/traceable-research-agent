# 交接文档：Pre-R13.2 Freeze Patch 发布复核

> 本文档用于跨会话交接。发布状态以远端 `feature/improvements` HEAD 与对应
> GitHub Actions 为准；本地通过不等于已发布，也不等于真实联网验收通过。

## 一、当前状态

- **分支**：`feature/improvements`
- **本轮基线**：`57d665d02b155c5a334f928c8da430b07a5098a7`
- **当前发布提交**：以 `git rev-parse HEAD` 与远端分支 HEAD 为准
- **当前阶段**：`Pre-R13.2 Freeze Patch`
- **当前结论**：修复方案限定的六项完整性补丁已实现并通过本地门禁；只有远端
  `feature/improvements` 最终 HEAD 的 GitHub Actions Green 后才可视为发布完成，
  且不得自行进入 R13
- **项目边界**：单实例、本地优先、SQLite／workspace 持久化、只读外部工具；
  不引入多租户、RBAC、分布式基础设施、向量数据库或通用 RAG
- **明确排除**：R13 Coverage／Gap／Evidence Gain／Research Requirement、Python
  Runtime、Embedding／Vector DB、Orchestrator 重写，以及 R10–R12 已稳定的 Budget
  Retry、Retry Freshness、Result API 与既有 Gate 语义变更

## 二、本轮限定实现

1. 新增 `report_claim_scope_group_links` 与 Alembic `0015`，把 Final Report Claim
   occurrence 到 `ScopeClaimGroup` 的 lineage 持久化；映射来源仅允许
   `citation_lineage`、`claim_member_lineage`、`text_fallback`，并覆盖幂等重建、
   旧完整 revision 回填和级联删除。
2. 将 Source 计数拆为 Resource Identity 与 Independence Identity：同一 canonical
   Resource 的多 Run／多 Snapshot 先合并为一个 Resource，独立来源再在该投影上分组；
   Scope reasoning 统一消费解析后的 independence alias。
3. 对显式且同一 wire service 的候选启用受控近重复归并：限定发布时间窗口、标题与
   正文相似度阈值、正文采样上限和每个 wire 的候选上限；未引入 Embedding、LLM、
   Vector DB 或全局无界 O(n²) 比较。
4. Scope 指标显式区分 `raw_source_count`、`unique_resource_count` 与
   `independent_source_count`；兼容字段 `effective_unique_source_count` 保持 Resource
   语义，passage 指标不变。
5. Citation Validator 与 Orchestrator 共用同一 citation-to-claim attachment helper；
   独立成行的 citation marker 可确定性附着到最近的前一 Final Claim，同时保持
   `## 3. 最终回答` 的 occurrence 边界不变。
6. 更新本交接文档与 `RELEASE_VALIDATION.md`，只记录可复现的本地结果与外部阻塞。

## 三、本地验证结果

- Python `compileall`：通过；`git diff --check`：通过。
- 完整 pytest：`848 passed / 2 skipped / 1 xfailed / 102 subtests passed / 0 failed`；
  24 条第三方弃用警告。
- 官方隔离离线入口：收集 843 项，`840 passed / 2 skipped / 1 xfailed / 0 failed`；
  Offline network guard 为 0 次外部尝试。
- 迁移：Alembic 单一 head 为 `0015_report_claim_scope_lineage`；Fresh DB `0001→0015`、
  Existing DB 升级、二次启动幂等、数据保留与 `alembic check` 均通过。
- 综合 Smoke：18/18 通过；隔离 Live API、Research Integrity、Provenance V2、
  Reasoning Capacity 与 Evidence Export 均通过，外部 API 调用为 0。
- OpenAPI：正式生成脚本连续执行两次，`web/src/api/schema.d.ts` 哈希均为
  `2ef993be0edcabed47e905de652667d23a14721655583fad7f69bacfa141d487`，零差异。
- 前端：typecheck、lint、11 个测试文件／106 项测试、production build、59 项 QA
  fixture isolation 检查全部通过。
- Streamlit：实际启动并通过 `/_stcore/health`，随后正常停止。
- Docker 静态配置 Smoke：通过。

前端测试仍输出既有 React `act(...)` 与 React Router v7 提示，但退出码为 0。

## 四、未完成与外部阻塞

1. 未执行真实 LLM、搜索、Browser、PDF 或 Remote Provider 请求；不得将 mock／离线
   结果描述为真实 Provider 验收通过。
2. 当前环境没有 Docker CLI，未执行 Compose 校验、镜像 build 或容器启动；静态
   Docker 配置 Smoke 已通过。
3. 发布成功必须以远端 `feature/improvements` 最终 HEAD 与其 GitHub Actions 为准，
   不能用本地 Commit 或本地测试结果代替。

## 五、下一步

1. 推送本轮原子提交到 `feature/improvements`，核对远端 HEAD 与本地一致。
2. 等待最终 HEAD 对应 GitHub Actions 的 `lightweight` 与 `frontend` jobs 全部 Green。
3. 在已配置 Provider 与 Docker 的目标环境分别补做真实 Runtime 与镜像验收。
4. R13 仅在用户另行明确指令后启动。

## 六、工程注意事项

1. `TASK.md`、`docs/`、`.env`、`workspace/` 运行产物是本地内容，不得提交。
2. Resource Identity 表示同一可寻址资源；Independence Identity 表示独立报道／发布
   链。两者不得重新混为同一 hash 或计数。
3. Wire-service 近重复只允许在显式、同 wire、受限候选集合内执行；不得扩大为全局
   模糊去重。
4. Final Claim 到 Scope Group 的映射优先级固定为 Citation lineage、Claim member
   lineage、text fallback；运行时 preview 与持久化 bundle 必须一致。
5. 不要把离线 Fixture、静态 Docker 检查或外部阻塞描述为真实联网验收通过。

(End of file)
