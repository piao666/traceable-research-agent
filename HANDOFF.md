# 交接文档：R12.1 本地收口完成、待提交与远端 CI

> 本文档用于跨会话交接。发布状态以远端 `feature/improvements` HEAD 与对应
> GitHub Actions 为准；本地通过不等于已发布，也不等于真实联网验收通过。

## 一、当前状态

- **分支**：`feature/improvements`
- **远端基线**：`b0edf9c4b1a121a18ec193b6977406c9d3f21738`（R10）
- **R12.1 收口前 HEAD**：`97e1ca6af0b51f37214d472150b547d1dea1f957`
- **当前收口提交**：包含本交接文档的最新本地提交；精确 SHA 以
  `git rev-parse HEAD` 为准
- **远端差异**：本地领先 `origin/feature/improvements` 21 个提交
- **当前阶段**：`R12.1 — Research Result Governance Stabilization`
- **当前结论**：R12.1.0～R12.1.13 主体与本轮收口缺口均已在本地实现，规定的
  离线后端、前端、OpenAPI、迁移和 Smoke 门禁通过；本轮修改已在本地提交但
  尚未推送，远端 CI 尚无结果，因此不得标记 `R12.1 COMPLETE`，不得进入 R13
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

## 三、本地验证结果

- Python `compileall`：通过。
- R12.1 指定专项集合：`382 passed / 1 xfailed / 21 subtests passed / 0 failed`。
- CI 同款离线 pytest：收集 793 项，`790 passed / 2 skipped / 1 xfailed / 0 failed`；
  Offline network guard 为 0 次外部访问。
- 默认 unittest 离线入口：`680 tests passed / 2 skipped / 0 failed`。
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
3. 本轮修改已提交；本地共领先远端 21 个提交，尚未推送，GitHub Actions 尚未运行。
4. 在远端功能分支 CI Green 前，不得标记 `R12.1 COMPLETE`，不得进入 R13。

## 五、下一步

1. 推送 `feature/improvements`，等待对应 GitHub Actions Green。
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
