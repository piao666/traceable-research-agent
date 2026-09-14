# 交接文档：Pre-R13 Independent Source 语义修正 + 已有故障修复

> 本文档用于跨会话交接。发布状态以远端 `feature/improvements` HEAD 与对应
> GitHub Actions 为准；本地通过不等于已发布，也不等于真实联网验收通过。

## 一、当前状态

- **分支**：`feature/improvements`
- **本轮基线**：`a334e21`（Pre-R13.2 Freeze）
- **当前 HEAD**：`f469103`（Pre-R13 Independent Source 语义修正）
- **当前阶段**：Pre-R13，等待 R13 启动指令
- **项目边界**：单实例、本地优先、SQLite／workspace 持久化、只读外部工具；
  不引入多租户、RBAC、分布式基础设施、向量数据库或通用 RAG

## 二、本轮实现

### P1：Improvement Source Quality 改用 independence_aliases

- **`app/improvement/evaluator.py`**：`_effective_source_tiers()` 不再按 `source_aliases`
  （Resource Alias）计数，改为直接使用 `scope_identity.independence_aliases`，每个
  independence cluster 最多贡献 1 个 Source。旧 bundle 无 `independence_aliases` 时
  fallback `source_aliases`。返回签名扩展为 `(tiers, count, computed_metrics | None)`，
  非 Scope Run 的 computed metrics 回流给 `auto_evaluate_and_log()` 使用。
- `auto_evaluate_and_log()` 新增 `unique_resource_count`、`independent_source_count`
  字段；`effective_source_count` 固定为 Independent Source 语义（兼容字段）。
- **`tests/test_improvement_frontend_contracts.py`**：既有测试补 `independence_aliases`
  + `metrics`；新增 `test_improvement_does_not_reward_syndicated_resource_duplicates`
  （3 Reuters Resources → 1 independent source）。

### P2：Scope Outcome 显式暴露两种语义

- **`app/research/outcome.py`**：`assess_scope_outcome()` 返回 dict 新增
  `unique_resource_count` 和 `independent_source_count`；`effective_source_count`
  保持 Resource Count 语义（向后兼容）。
- **`tests/research/test_scope_outcome.py`**：新增
  `test_scope_outcome_exposes_resource_and_independent_source_counts`。

### 语义一致性

| 层 | 3 Reuters Resources → | 关键字段 |
|---|:--:|------|
| Improvement Evaluator | **1** | `effective_source_count=1`, `independent_source_count=1` |
| Scope Outcome | **1** | `independent_source_count=1`, `unique_resource_count=3` |
| Scope Identity | **1** | `independence_aliases` 1 cluster（`member_document_ids` 含 3 docs） |

### 已有故障修复（5 处）

| # | 文件 | 修复 | 根因 |
|---|------|------|------|
| 1 | `tests/test_r8_recovery.py:87` | `Path.read_text(encoding="utf-8")` | Windows 默认 GBK 编码 |
| 2 | `tests/test_r8_context_budget.py:249` | 同上 | 同上 |
| 3 | `tests/test_r9_goal_recovery.py:656` | 同上 | 同上 |
| 4 | `tests/test_phase5.py:418` | `Settings().deep_research_enabled` 替代全局 `settings` | `.env` 污染默认值断言 |
| 5 | `tests/test_r7_release.py` | `try/finally db.close()` + `TemporaryDirectory(ignore_cleanup_errors=True)` | Windows SQLite 文件锁 |

## 三、本地验证结果

- **compileall**：通过。
- **完整 pytest**：**850 passed / 2 skipped / 1 xfailed / 102 subtests passed / 0 failed**。
- **离线测试**：675 项，674 passed / 1 failed（仅有 `test_demo_restart` Windows temp
  文件锁残留，非代码逻辑问题）、2 skipped。
- **Docker 静态配置**：通过。
- **Secrets audit**：无敏感文件暂存。

## 四、未完成与外部阻塞

1. 未执行真实 LLM、搜索、Browser、PDF 或 Remote Provider 请求；不得将 mock／离线
   结果描述为真实 Provider 验收通过。
2. 当前环境没有 Docker CLI，未执行 Compose 校验、镜像 build 或容器启动；静态
   Docker 配置 Smoke 已通过。
3. 后续任何代码变更仍必须以新的远端 HEAD 与其 GitHub Actions 为准，不能沿用本轮
   结果替代新提交的验证。

## 五、下一步

1. R13 仅在用户另行明确指令后启动；R13 中 Coverage／Evidence Gain／Source Diversity
   应统一使用 `independent_source_count`，不再使用 `effective_source_count`。
2. 在已配置 Provider 与 Docker 的目标环境分别补做真实 Runtime 与镜像验收。

## 六、工程注意事项

1. `TASK.md`、`docs/`、`.env`、`workspace/` 运行产物是本地内容，不得提交。
2. Resource Identity ≠ Independence Identity：同一 Reuters story 的 3 个转载是
   3 个 Resource、1 个 Independent Source。两者不得重新混为同一 hash 或计数。
3. Improvement `effective_source_count` 现在固定为 Independent Source 语义；
   Scope Outcome 的 `effective_source_count` 仍为 Resource Count（向后兼容）。
4. Windows 上所有 `Path.read_text()` 必须显式 `encoding="utf-8"`，否则中文内容
   触发 GBK 解码错误。
5. `Settings()` 无参构造取 Pydantic 字段默认值；全局 `settings` 单例受 `.env`
   污染，不可用于"默认值"断言。

(End of file)