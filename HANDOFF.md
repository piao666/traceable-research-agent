# 交接文档：Pre-R13 Independent Source 语义修正 + 已有故障修复

> 本文档用于跨会话交接。发布状态以远端 `feature/improvements` HEAD 与对应
> GitHub Actions 为准；本地通过不等于已发布，也不等于真实联网验收通过。

## 一、当前状态

- **分支**：`feature/improvements`
- **本轮基线**：`70f4eb0`（远端 feature/improvements 基线）
- **当前 HEAD**：`318dba5`（统一研究终态裁决与 Quick/Deep 证据边界）
- **当前阶段**：Pre-R13 收尾；代码已提交并推送到远端 `feature/improvements`，等待人工 Quick/Deep 验收
- **项目边界**：单实例、本地优先、SQLite／workspace 持久化、只读外部工具；
  不引入多租户、RBAC、分布式基础设施、向量数据库或通用 RAG

## 二、本轮实现

### P0：官方信源恢复与执行链修正（2026-09-14）

- `app/agent/source_governance.py`：动态官方信源发现、多查询恢复与每次 run 的上下文。
- `app/agent/executor.py`、`app/agent/react_executor.py`：接入定向恢复、展平元数据并保留 React 状态。
- `tests/test_source_governance.py`：重写 vendor 场景测试并补充 E2E 回归覆盖。
- 提交：`dbbf490`、`52bd74b`。

### 测试夹具修复与全量验证（2026-09-15）

- `tests/test_phase8.py` 改用策略配置中的通用已验证仓库夹具，恢复 GitHub 证据角色测试与当前生产策略的一致性。
- `tests/test_research_integrity.py` 的 ReAct Mock 补充 `structured_complete` 响应，匹配当前执行器接口。
- 完整 pytest：**870 passed / 2 skipped / 1 xfailed / 100 subtests / 0 failed**。
- 新增 3 个回归测试覆盖上述治理继承、URL 升级和 T2 比例恢复路径；最新完整 pytest：**873 passed / 2 skipped / 1 xfailed / 100 subtests / 0 failed**。
- R13 仍暂不启动；当前阻塞已从 pytest 失败转为等待后续启动指令及真实环境验收。

### Deep Research V2 正式 E2E 前四项修复（2026-09-15）

- ResearchNode 子计划现在继承父 Run 的 `retrieval_profile`、`profile_constraints` 和 `policy_version`。
- 搜索结果中的 `official`、`source_tier`、`source_class` 等治理字段贯通 EvidenceItem 与 SourceDocument。
- 同一 canonical URL 的跨轮结果合并时，后续更高质量治理结果会升级并保留元数据。
- `t2_ratio_exceeded` 纳入 quota shortfall，触发 targeted recovery，直到配额满足或预算耗尽。
- 完整 pytest：**870 passed / 2 skipped / 1 xfailed / 100 subtests / 0 failed**。

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
- **最近一次已记录的完整 pytest**：**850 passed / 2 skipped / 1 xfailed / 102 subtests passed / 0 failed**；该结果早于当前 HEAD，不能替代当前完整 pytest 验证。
- **离线测试**：675 项，674 passed / 1 failed（仅有 `test_demo_restart` Windows temp
  文件锁残留，非代码逻辑问题）、2 skipped。
- **Docker 静态配置**：通过。
- **Secrets audit**：无敏感文件暂存。
- **当前状态**：完整 pytest 已通过；仍未执行真实 Provider、Docker 和 Browser/PDF 联网验收。

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
2. 补做正式 UI/API E2E 与真实 Runtime、镜像验收；R13 仍需用户明确启动指令。

## 六、记录同步说明

- `TASK.md` 中历史条目保留为审计记录；“待完成：暂存 → 提交 → 推送”仅对当时尚未提交的记录有效，不代表当前 HEAD 仍待提交。
- 本轮测试夹具与文档修订应随当前提交发布；本轮发现的两个未跟踪文件已删除。

## 七、工程注意事项

1. `TASK.md`、`docs/`、`.env`、`workspace/` 运行产物是本地内容，不得提交。
2. Resource Identity ≠ Independence Identity：同一 Reuters story 的 3 个转载是
   3 个 Resource、1 个 Independent Source。两者不得重新混为同一 hash 或计数。
3. Improvement `effective_source_count` 现在固定为 Independent Source 语义；
   Scope Outcome 的 `effective_source_count` 仍为 Resource Count（向后兼容）。
4. Windows 上所有 `Path.read_text()` 必须显式 `encoding="utf-8"`，否则中文内容
   触发 GBK 解码错误。
5. `Settings()` 无参构造取 Pydantic 字段默认值；全局 `settings` 单例受 `.env`
   污染，不可用于"默认值"断言。

## 2026-09-15 Planned Executor 修复

- 修复 `app/agent/executor.py` targeted refetch 后调用未定义 `_observation` 的 NameError，并移除会造成重复 observation、tool count 和 latency 的重复更新。
- `.venv` 全量验证：**873 passed, 2 skipped, 1 xfailed, 24 warnings, 100 subtests passed**（约 3 分 34 秒）。
- Windows pytest cache 仍报告 WinError 5 权限警告；使用仓库内 `--basetemp` 可正常完成，警告不影响退出码。

## 2026-09-15 Docker 构建失败修复

- **根因**：`python:3.11-slim` 浮动到 Debian Trixie 后，`playwright install --with-deps chromium` 获取 `trixie/main` apt 索引返回 404，构建在 Dockerfile 第 26 行失败。
- **修复**：将基础镜像固定为 `python:3.11-slim-bookworm`，保持 Playwright 安装步骤不变。
- **验证**：`tests/test_r11_retrieval_runtime.py` → 5 passed；`docker compose config` 应作为人工复核前的静态检查。完整镜像重建已开始但因基础层下载极慢中止，未宣称构建成功。
- 后续复核日志显示 HTTPS apt 已成功，失败点转为 Chromium 184 MB CDN 下载连接被关闭；已设置 `PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT=300000` 允许慢速链路完成下载。
- 该超时和备用 CDN 仍分别表现为 CDN 断连与 HTTP 400；最终改为安装 Debian Bookworm 的系统 `chromium` 包，并通过 `PLAYWRIGHT_EXECUTABLE_PATH=/usr/bin/chromium` 供 Playwright 使用。API、Streamlit、Web 镜像均已构建并启动；`/health`=ok、`/api/tools`=12、Web/Streamlit HTTP 200。

## 2026-09-20 统一终态裁决与人工验收准备

- **远端提交**：`318dba5`，父提交为 `70f4eb0`；本地 Git HTTPS 凭据不可用，已通过授权的 GitHub 连接器完成远端文件树写入和 `feature/improvements` 引用更新。
- **终态契约**：Run 的最终状态统一由 `finalize_terminal_decision()` 裁决；`completed`、`incomplete`、`failed`、取消和人工等待状态不再由独立诊断模块直接覆盖。
- **Quick 边界**：discovery Quick 只允许确定性的来源标题/URL 报告；搜索摘要保持 `discovery_index`，不得独立支持实质性结论；substantive Quick 缺少正文证据时进入 `incomplete`。
- **Deep 状态**：Branch planner、Research Scope、root Run 和最终报告统一回写终态；planner 异常会同步节点/Scope 状态，重试和恢复不会继承旧 Scope、Gate 或报告哈希。
- **能力准入**：按任务需要区分 search、full_text、browser、PDF、academic 和 structured data；配置、探测失败和未知状态分别处理，单个 URL 的 403 不再误判为全局后端不可用。
- **报告/API/UI**：`incomplete` 报告可读取并标记为 partial；报告被修改后，API 下载和 SSE `report_ready` 均会被最终哈希校验阻断；后置完成状态审计不会改变 `## 3. 最终回答` 的 occurrence 范围。
- **离线验收**：官方离线 runner 退出码 0；**923 passed / 2 skipped / 1 xfailed / 24 warnings / 87 subtests / 0 blocked external attempts**。`compileall`、`git diff --check` 通过。前端 typecheck、lint、build 和 107 项测试通过。
- **Docker 人工验收**：已解决 Docker Hub 代理解析问题，`node:22-alpine` 和 `nginx:1.27-alpine` 可拉取，React Web 镜像已构建。一次全量 Compose 构建在 `python:3.11-slim-bookworm` 的 Docker Hub 认证阶段超时，需优先执行 `docker compose up --build -d api web` 完成 React 人工验收；Streamlit 镜像属于可选旧界面，可单独重试。
- **真实运行限制**：离线测试不等于真实 Provider 验收；Quick/Deep 人工测试必须在 `.env` 中填写真实 `LLM_BASE_URL`、`LLM_MODEL`、`LLM_API_KEY` 和 `TAVILY_API_KEY`。Docker 代理只负责镜像下载，不提供模型或搜索密钥。

### 人工验收入口

- React：`http://localhost:5173`
- API 健康检查：`http://localhost:8000/health`
- API 文档：`http://localhost:8000/docs`
- Streamlit（可选）：`http://localhost:8501`

### 人工验收建议

1. 先执行 `docker compose up --build -d api web`，确认 `docker compose ps` 中 API healthy、Web running。
2. Quick：选择 discovery/source lookup 类问题，确认来源列表和“未完成正文核验”提示；再用需要精确数字或正文结论的问题，确认 snippet-only 证据不会显示为完整完成。
3. Deep：选择需要多来源正文核验的问题，确认 Trace、引用、Scope 状态和最终报告一致；若 Provider 不可用，确认 Run 明确进入 failed/incomplete，而不是 completed。
4. 验收结束后用 `docker compose logs --tail 150 api` 检查是否存在 provider、配置或终态裁决错误。

(End of file)
