# 交接文档：R12.1 发布复核与远端 CI

> 本文档用于跨会话交接。发布状态以远端 `feature/improvements` HEAD 与对应
> GitHub Actions 为准；本地通过不等于已发布，也不等于真实联网验收通过。

## 一、当前状态

- **分支**：`feature/improvements`
- **远端合并基线**：`8021aff8442d18760c8ea52b964442def2e66115`（R12）
- **导入的 R12.1 提交包末端**：`5c2f2befb456e4d49238c6387b83285ef65a7025`
- **历史汇合提交**：`ed0be3118d57e53475ded99313e8105dd64dd4db`；第一父提交为
  `5c2f2be`，第二父提交为 `8021aff`，合并文件树与 `5c2f2be` 完全一致
- **当前发布提交**：包含本交接文档的 `feature/improvements` 最新提交；精确 SHA
  以 `git rev-parse HEAD` 与远端分支 HEAD 为准
- **当前阶段**：`R12.1 — Research Result Governance Stabilization`
- **当前结论**：R12.1.0～R12.1.13 主体与本轮复核缺口均已实现；规定的离线后端、
  前端、OpenAPI、迁移和 Smoke 门禁均须以本节记录与当前提交 CI 为准。远端目标
  分支已通过普通合并纳入既有 R11/R12 历史，不使用 Force Push。只有当前远端 HEAD
  的 GitHub Actions Green 后才可标记 `R12.1 COMPLETE`，且不得自行进入 R13
- **项目边界**：单实例、本地优先、SQLite／workspace 持久化、只读外部工具；
  不引入多租户、RBAC、分布式基础设施、向量数据库或通用 RAG
- **明确排除**：R13、R14、R15 扩展功能与 `LICENSE`

## 二、本轮 R12.1 收口实现

1. 修复 `smoke_reasoning_capacity.py` 的 SQLAlchemy metadata 注册缺口，容量
   Smoke 可在 10,000 条 Claim-Evidence 边上完成索引查询验收。
2. 修复 `smoke_evidence_export.py`：使用独立临时数据库和离线自包含配置，适配
   Scope-aware Provenance 的 `source_documents`／`passages` 导出结构；失败工具输出
   不作为证据，limitation 与 Result Trace 仍保留审计信息。
3. 将 Evidence Role 纳入 Citation／Report Gate：`official_metadata` 与
   `discovery_index` 只能单独证明文献存在、DOI、作者、年份与出版信息，不能单独
   证明实验结果、性能数值或研究结论；二次 LLM 判断不能覆盖该确定性限制。
4. 打通 Orchestrator 重入恢复：已有 `pending`／`running` Child Node 会进入
   `ResearchNodeExecutor` 的幂等恢复路径，复用原 Child Run，不创建重复 Run，并在
   完成后继续分支规划和最终 Scope Gate。
5. 新增方案要求的统一 Integrated Scope Fixture，在一条 Root + Child A/B/C 链上
   同时覆盖同源数值冲突、Reuters story identity、Academic DOI、raw/effective
   alias、Scope reasoning、CitationOccurrence、Child origin、Result API／Trace、
   Scope Export、Report Integrity 与 Improvement Evaluator。
6. 修复取消回归用例的测试顺序依赖：用例显式注入最小 `file_reader` ToolSpec 与
   Readiness 条件，独立运行时也能真实进入“工具调用期间取消”路径，不依赖全局
   Tool Registry 已被其他测试预先初始化。
7. 更新中英文 README、Result API 清单和发布验证文档，使 R11／R12 的已发布状态、
   R12.1 结果语义、迁移头与当前测试基线一致。

## 三、本地验证结果

- Python `compileall`：通过。
- 完整 pytest：`798 passed / 2 skipped / 1 xfailed / 102 subtests passed / 0 failed`；
  15 条第三方弃用警告。
- 官方隔离离线入口：收集 793 项，`790 passed / 2 skipped / 1 xfailed / 0 failed`；
  Offline network guard 为 0 次外部尝试。
- 取消状态回归独立执行：`1 passed`。
- 强制 Smoke：`smoke_research_integrity.py`、`smoke_provenance_v2.py`、
  `smoke_reasoning_capacity.py`、`smoke_evidence_export.py` 全部通过。
- OpenAPI：连续生成两次，`web/src/api/schema.d.ts` 均为零差异。
- 前端：typecheck、lint、11 个测试文件／106 项测试、production build 全部通过。
- 迁移：专项测试覆盖 Fresh DB `0001 → 0013` 与 Existing DB `0012 → 0013`；
  Fresh DB Smoke 完成全量迁移。
- Streamlit 与 Docker 配置静态 Smoke：通过。
- `git diff --check`：通过。

前端测试仍输出既有 React `act(...)` 与 React Router v7 提示，但退出码为 0。

## 四、未完成与外部阻塞

1. 当前环境未配置 Reporter LLM、Actor LLM、Tavily，且 Deep Research 未启用；
   Real Runtime Smoke 只能记录为 `external runtime blocked`，不能写成真实验收通过。
2. 当前执行环境没有 Docker CLI，未执行镜像实际 build／start；静态 Docker 配置
   Smoke 已通过，仍需在 Windows Docker Desktop 环境人工核验。
3. 发布是否成功必须以远端 `feature/improvements` HEAD 与其 GitHub Actions 为准，
   不能用本地 Commit 或本地测试结果代替。
4. 在当前远端功能分支 HEAD 的 CI Green 前，不得标记 `R12.1 COMPLETE`，不得进入 R13。

## 五、下一步

1. 核对 `feature/improvements` 远端 HEAD，并等待该提交对应 GitHub Actions Green。
2. 在已配置 Provider 的真实环境执行至少一个包含 Child Node 的 Deep Research
   Runtime Smoke；若 Provider 仍不可访问，继续如实记录 `external runtime blocked`。
3. 在 Windows Docker Desktop 重建并人工核验 Result Evidence、Trace、Export 与
   Child Citation 跳转。
4. 只有全部强制 DoD 与远端 CI 满足后，才更新为 `R12.1 COMPLETE`；之后是否进入
   R13 必须等待用户明确指令。

## 六、工程注意事项

1. `TASK.md`、`docs/`、`.env`、`workspace/` 运行产物是本地内容，不得提交。
2. 失败、拒绝、内部诊断与硬预算耗尽保留在 Trace，但不得伪装为有效 Evidence。
3. Scope 结果必须继续保留 `origin_run_id`、`origin_trace_id` 与
   `research_node_id`，不得把 Child Evidence 复制为 Root 所有。
4. Metadata Index 的高 Authority 不等于 Primary Evidence；Evidence Role 的证明
   范围 Gate 不得被文本重合度或 LLM 二次判断绕过。
5. 不要把离线 Fixture、静态 Docker 检查或外部阻塞描述为真实联网验收通过。

(End of file)
