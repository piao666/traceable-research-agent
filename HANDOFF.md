# 交接文档：R12 Deep Research Engine V2 本地验收完成、待提交

> 本文档用于跨会话交接。发布状态以远端 `feature/improvements` HEAD 与对应
> GitHub Actions 为准；本地通过不等于已发布或已完成真实联网验收。

## 一、当前状态

- **分支**：`feature/improvements`
- **远端已发布基线**：`b0edf9c4b1a121a18ec193b6977406c9d3f21738`（R10）
- **本地 R11 提交**：`5a32482`，因当前环境缺少 GitHub HTTPS 凭据尚未推送
- **当前阶段**：`R12 — Deep Research Engine V2 Replacement`
- **R12 状态**：实现与离线门禁完成，修改尚未提交或推送
- **项目边界**：单实例、本地优先、SQLite／workspace 持久化、只读外部工具；
  不引入多租户、RBAC、分布式基础设施、向量数据库或通用 RAG
- **明确排除**：R13 研究智能、R14 长报告与崩溃恢复、R15 真实验收、`LICENSE`

## 二、R12 已完成内容

1. `AgentRun` 增加显式 `parent_run_id`、`root_run_id`、`run_role`、
   `research_scope_id`、`engine_version`，默认列表隐藏内部研究节点。
2. 新增迁移 `0012_research_scope_and_lineage`，保守回填旧 Run，并持久化
   `ResearchScope`、`ResearchNode`；DB lineage 是唯一权威，旧 `plan_json`
   字段只作兼容投影。
3. 新增 Research Scope Resolver、Tree 投影、节点执行器与 Scope 级预算统计；
   所有后代 Run 复用根 Run 的既有 `RunBudget`。
4. Deep Profile Dispatcher 直接进入 Engine V2 Research Orchestrator；Standard 与
   Offline 继续使用原路径。节点复用 ReAct、Tool Registry、执行策略、恢复、Trace
   与 Evidence Pipeline。
5. 子节点结束时只固化证据，不生成中间主体报告；根 Run 在 Scope 质量门通过后，
   使用父子全部 Evidence 生成唯一最终报告。
6. Scope Evidence 只做跨 Run 逻辑聚合，不复制子 Run 数据；聚合实体保留
   `origin_run_id`、`origin_trace_id`、`research_node_id`，引用可反查到子 Trace。
7. 增加 Scope Outcome、Citation、Reasoning、Reference adapter，以及
   `research-scope`、`research-tree`、`scope-evidence` 三个只读 API。
8. `app.agent.deepening.run_deepening` 仅保留一个兼容周期的弃用 wrapper；正式
   Dispatcher 不再调用旧 Round 引擎，私有旧实现只用于历史回归定位。

## 三、R12 验证结果

- R12 专项：`23 passed`，另有 1 项严格 `xfail` 为 R14 长报告上下文缺陷。
- 完整离线 pytest：收集 717 项，`714 passed / 2 skipped / 1 xfailed / 0 failed`；
  13 条第三方弃用警告；离线网络守卫记录 0 次外部访问。
- 前端：OpenAPI 契约已同步；类型检查、Lint、11 个测试文件／104 项测试、生产构建通过。
- 迁移：全量升级至 0012、重复升级、Alembic autogenerate check、SQL 安全解析通过。
- 综合 Smoke：18/18 通过，应用本地评估通过。
- 其他：Python `compileall`、研究完整性 Smoke、Docker 静态配置 Smoke、
  `git diff --check` 通过。
- 未执行：Docker 镜像实际构建／启动、Windows 前端人工验收、真实 LLM／搜索／抓取；
  当前结果不得表述为真实联网验收通过。

## 四、R12 核心验收链

核心回归构造 Parent Evidence A 与 Child Evidence B，并确认：

```text
Scope Bundle 同时包含 A / B
→ 最终报告可使用 B 的 CIT 标签
→ Citation(B) 解析到 Child Passage
→ Child Passage 解析到 Child Snapshot / Trace / origin_run_id
```

子 Evidence 始终归子 Run 所有，没有以 Parent `run_id` 再写一份。

## 五、下一步

1. 审阅 R12 差异后提交；先解决 GitHub 凭据再推送 R11 与 R12，并观察功能分支 CI。
2. 用户若确认进入下一阶段，再按方案实施 R13；不要在 R12 提前加入 Coverage／Gap、
   Conflict Verification、结构化数据工具或停止策略。
3. R14 再处理分章节 Report Composer、7000 字符旧上下文限制、Checkpoint／Resume 与清理策略。
4. R15 和人工阶段再执行真实 Provider、Browser／PDF／Remote Extract 与 Docker 验收。
5. `LICENSE` 按用户要求暂不处理。

## 六、工程注意事项

1. `TASK.md`、`docs/`、`.env`、`workspace/` 运行产物是本地内容，不得提交。
2. 文件读取继续保持路径边界、逐文件 HITL 和长度／体积限制。
3. 外部系统写操作不得通过 MCP 暴露；本地持久化副作用必须在工具元数据中如实声明。
4. 每次工具执行必须保留 Trace；失败、拒绝和硬预算耗尽不得被报告生成覆盖。
5. 不要把固定数据、mock 回归或静态 Docker 配置检查描述成真实环境验收。

(End of file)
