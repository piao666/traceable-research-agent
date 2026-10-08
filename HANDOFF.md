# Traceable Research Agent 交接 — 2026-10-08

## 当前检查点

- 分支：`feature/improvements`。本轮整合研究闭环、引用/覆盖、预算恢复与界面修复；中英文 README 已同步。
- 核验闭环：**研究义务 → 对象与维度 → 具体缺口 → 对应行动 → 完成确认**。
- 最新后端冻结源码：1374 passed、1 xfailed、86 subtests passed；22 个既有警告。闭环专项：152 passed。
- 前端 100 项测试通过，类型检查、生产构建与 Lint 通过；Lint 排除本地不可读 pytest 缓存，未删除该缓存。
- 隔离 API smoke passed，external_api_calls=0；compileall、diff 和 Compose config 通过。
- Docker API/Streamlit 已重建，176 个 Python 源文件与本地哈希一致。部署前后 202 个既有 Run、55 份报告和历史表指纹不变，迁移为 `0019_research_work_state`。
- 真实 Quick/Deep 多轮出现未完成或内容误放行；历史 completed 不能证明本轮内容验收成功。最新修复后的完整实联网内容验收和用户人工复核仍待完成。

## 生效约束

`AGENTS.md` 与本地 `TASK.md` 是约束和进度来源。`TASK.md`、`CLAUDE.md`、`docs/`
只保留本地，不提交。此次 HANDOFF 为可版本化的简明交接；详细取证、原题、Run ID、
测试日志和历史指纹均位于本地记录。公共示例使用虚构对象，不提交密钥或数据库。

工具仍经 Registry/Policy、根共享预算与 Trace 执行。项目不扩展租户隔离、RAG、
向量索引或写外部状态功能。审批仅对指定操作或指定 Run 生效。

## 已定位根因与修复

| 环节 | 根因 | 当前实现 |
| --- | --- | --- |
| 义务 | 一对多问题义务被覆盖；报告删答后未必重新检查 | 稳定义务身份、多项要求、每次当前回答覆盖检查 |
| 对象/维度 | 来源候选池被当成必答清单；同名或定位标签误替代具体对象/任务 | 来源身份、固定对象×维度、独立成员类别与应用任务复核 |
| 缺口 | 本地拒绝原因未传递；映射格式与实质缺证混用 | 逐项原因、同证据有界映射修复、持久缺口账本 |
| 行动 | 旧多对象目录挤出新单对象正文；窗口未变仍反复综合 | 逐单元投影、真实 Trace 绑定、无视图变化跳过重判、复用单元补证额度 |
| 确认 | 获取成功、模型自报 complete 或引用通过被当作全部回答完成 | 当前报告字节/引用位置/身份/条件/逐项覆盖及不可变独立裁决证明 |

有效候选单元保存精确窗口哈希及坐标；后续综合保留已有发现但移除旧引用标签。
负语义或负身份结果不能保留为完成候选。模型提供方错误单独留存并停止循环，
不作为缺证持续补搜。历史记录不重写，也不按新规则自动重新判定。

## 源码入口

| 链路 | 主要文件 |
| --- | --- |
| 契约与对象范围 | `app/research/task_understanding.py`、`comparison_scope.py`、`referential_scope.py`、`state.py` |
| 工作循环与补证 | `app/research/controller.py`、`control_store.py`、`recovery.py`、`orchestrator.py`、`node_executor.py` |
| 写作投影与发现 | `app/reporting/writing_evidence.py`、`app/research/findings.py` |
| 引用、覆盖与独立审计 | `app/evidence/citation_validator.py`、`decision_audit.py`、`app/research/answer_coverage.py`、`application_review.py` |
| 终稿修订与结果 | `app/agent/reporter.py`、`app/reporting/revision_pipeline.py`、`app/research/outcome.py` |
| 预算审批及重试 | `app/agent/budget.py`、`app/api/tasks.py`、`app/schemas.py` |
| 持久化 | `app/research/models.py`、`migrations/versions/0019_research_work_state.py` |
| UI | `web/src/components/ResearchWorkPanel.tsx`、`RunLayout.tsx`、`web/src/pages/ReportPage.tsx` |
| 闭环回归 | `tests/research/test_cell_projection_loop.py`、`test_research_work_loop.py`、`test_token_budget_approval.py` |

## 预算与部署

Deep 示例上限：400000 Token、192 次逻辑 LLM 调用；显式旧配置不会自动更新。
Token 和调用次数分别审批，不限 Token 不扩充调用、工具、时间或成本额度。
暂停/续跑不清零历史计数；`resume: false` 保持预算时钟暂停。
研究阶段不能消费终稿保留额度，终稿调用消耗会扣减剩余额度。

常规部署先备份数据，再执行 `docker compose up --build -d`。
默认 Web/API/Streamlit 端口为 5173/8000/8501；当前本机隔离验收入口为
`http://127.0.0.1:15173`、API `http://127.0.0.1:18000/health`、
Streamlit `http://127.0.0.1:18501`。本地覆盖配置在 docs，不纳入公共部署要求。
仅 restart 不会重新构建镜像；在途 Run 不进行源码热替换。

## 下一步人工核验

1. 用新版新建 Quick/Deep 原题重试，保留旧任务及报告作为对照。
2. 对每项必答义务核对对象身份、维度及原文支持；清单、具体任务、内部实现与条件分别检查。
3. 逐个缺口核对补证动作、Trace 和写作窗口是否变化；采集成功必须随后形成当前回答证明。
4. 审阅最终完整报告，确认没有删掉必答项、借用对象或把部署定位当作业务任务。
5. 必要预算审批在对应 Run 单独完成，保留用量和溯源；记录实际终态及未完成原因。

真实提供方验收未通过前，不将单元测试、离线投影、HTTP 200 或历史 completed
记作内容成功。详细根因与验证记录见本地 TASK 及 docs/project-status。
