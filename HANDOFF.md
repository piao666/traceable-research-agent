# 交接文档：12 项缺陷修复（进行中）

> 本文档用于跨会话交接。前一个会话已完成 12 项缺陷的**代码修复**与**主体验证**，
> 剩余收尾（补测试、记录 TASK.md、最终提交）留给新会话完成。

---

## 一、背景与目标

- **项目**：Traceable Research Agent（自托管、可审计深度研究 Agent）
- **分支**：`feature/improvements`（工作区有 **18 个已修改 + 2 个新增** 文件，均**未提交**）
- **目标**：修复一次深度评审发现的 12 个缺陷，跑通验证后按 AGENTS.md 纪律提交。
- **附带修复**：`TASK.md` 原本是「UTF-8 前段 + GBK 尾段」的混合编码，已无损转为纯 UTF-8。

## 二、已完成的修复（代码已改，未提交/未推送）

| # | 缺陷 | 修复 | 关键文件 |
|---|---|---|---|
| 1 | 事件循环阻塞（异步端点里同步执行重负载） | `create_task`/`run_task`/`confirm_task`/`approve_plan`（tasks.py）与 `execute_tool`（tools.py）从 `async def` 改为 `def`，交给 FastAPI 线程池 | `app/api/tasks.py`、`app/api/tools.py` |
| 2 | SSRF 防护不完整 | 新建统一模块 `app/tools/ssrf.py`：补全私有/保留网段（含 `169.254.0.0/16` 云元数据、`::ffff:0:0/96`、`fe80::/10` 等）；识别整数/十六进制/八进制 IP 简写及**点分八进制/十六进制**（如 `0177.0.0.1`、`0x7f.0.0.1`）；对域名做 `getaddrinfo` 解析校验（DNS rebinding）；web_fetcher 与 pdf_reader 改为 `follow_redirects=False` + 手动逐跳校验（消除重定向 TOCTOU） | `app/tools/ssrf.py`（新增）、`app/tools/web_fetcher.py`、`app/tools/pdf_reader.py` |
| 3 | `source_policy_path` 默认值 v1/v2 打架 | `Settings.from_env()` 默认从 `config/source_policy.v1.json` 改为 `v2.json`（与模型字段、`.env.example`、`.env` 一致） | `app/config.py` |
| 4 | HITL 文件审批令牌可伪造 | `file_reader_execution_arguments` 注入 `_approved_file_reader_path` 前先 `pop` 掉计划里已有的同名字段 | `app/agent/file_access_policy.py` |
| 5 | 响应大小限制可绕过（只信 content-length） | web_fetcher/pdf_reader 改为流式 `response.iter_bytes` 强制 `max_response_bytes` 上限 | `app/tools/web_fetcher.py`、`app/tools/pdf_reader.py` |
| 6 | read-only 非结构性强制 | `ToolSpec` 新增 `read_only`/`side_effect_free` 字段并在 defaults.py 显式设置；`is_tool_read_only` 优先读字段；`is_executable_tool` 强制校验 | `app/tools/base.py`、`app/tools/defaults.py`、`app/mcp/policy.py`、`app/agent/executor.py` |
| 7 | 信源治理只查 T0 且吞异常 | `_check_profile_quota` 改为全维度校验（T0/T1/T2/独立簇/单域上限/T2 比例）+ 异常走 `logging.warning` | `app/agent/executor.py` |
| 8 | 孤儿 `.pyc` | 删除 `app/rag/`（仅剩 10 个 .pyc，无 .py 源） | 本地删除 |
| 9 | `EXECUTABLE_TOOLS` 死代码 + "Day N" 文案 | `EXECUTABLE_TOOLS` 变为 `is_executable_tool` 的真实白名单（补齐 `memory_search`/`pdf_reader`）；清理 6 处 "Day4/5/6/13-15/29" 文案 | `app/agent/executor.py`、`app/tools/registry.py`、`app/tools/file_reader.py`、`app/api/tasks.py`、`app/api/tools.py` |
| 10 | 过度脱敏（token 误伤指标字段） | `is_sensitive_key` 不再把 `token_in`/`token_out`/`token_usage`/`total_tokens` 等指标当密钥 | `app/security/redaction.py` |
| 11 | sql_safety 缺口 | DANGEROUS_KEYWORDS 补 `LOAD_EXTENSION/READFILE/WRITEFILE`；`_strip_quoted_content` 先剥字符串再剥注释；`_query_with_limit` 用正则识别真实 LIMIT 子句 | `app/tools/sql_safety.py`、`app/tools/sql_query.py` |
| 12 | requirements 把 pytest 当运行时依赖 + CI 引用失效文件 | 移除运行时 `pytest`，新增 `requirements-dev.txt`；`ci.yml` 改为装 `requirements.txt`+`requirements-dev.txt`，删除已不存在的 `scripts/smoke_planner_guardrails.py` 引用 | `requirements.txt`、`requirements-dev.txt`（新增）、`.github/workflows/ci.yml` |

**回归修复**：因把 `approve_plan`/`create_task` 改为同步 def，`tests/test_phase7.py` 里 3 处 `asyncio.run(...)` 调用已改为直接调用（并移除未使用的 `import asyncio`）。

## 三、当前验证状态

- ✅ `python -m compileall -q app scripts frontend migrations tests`：通过（exit 0）
- ✅ **pytest 完整**：`314 passed + 17 subtests`；`34 failed + 5 errors` **全部是沙箱 `tempfile` 权限问题**（fetch_cache / artifact_store / skill_loader / p2_reasoning），与修改前基线**完全一致 → 零净回归**。在正常环境（无沙箱）应全部通过。
- ✅ **pytest 定向**（test_phase7 / test_p0_runtime / test_contracts / test_phase2 / test_phase8 / test_phase8_5 / test_source_governance / test_routing / test_audit_fixes）：`131 passed + 7 subtests`
- ✅ **SSRF 新向量已验证**（`python -c` 直接调用）：`169.254.169.254`、`2130706433`（十进制）、`0x7f000001`（十六进制）、`0177.0.0.1`（点分八进制）、`0x7f.0.0.1`（点分十六进制）、`[::ffff:127.0.0.1]`（IPv4-mapped）、`[fe80::1]`（link-local）等全部 BLOCK。
- ⚠️ `scripts/smoke_final_project.py` **无法在当前沙箱运行**（内部用 `subprocess.run` 触发 `CreatePipe` 被沙箱拦截 `WinError 5`）。这是环境限制，非代码问题，需在正常环境重跑。

## 四、剩余待办（新会话需完成）

1. **补针对性测试**（建议，非强制）：
   - SSRF 新向量：扩展 `tests/test_phase2.py::WebFetcherTests::test_rejects_private_ip`，加入 `169.254.169.254`、`2130706433`、`0177.0.0.1` 等。
   - 脱敏：在 `tests/test_p0_runtime.py` 增加断言，确认 `token_in`/`total_tokens` 等指标**不被**脱敏，而 `token`/`github_token` 仍被脱敏。
   - HITL 令牌：新增测试确认 `file_reader_execution_arguments` 会剥离计划里注入的 `_approved_file_reader_path`。
2. **记录到 `TASK.md`**（local-only，已是 UTF-8）：追加本次 12 项修复的执行记录（变更文件、命令、结果、已知限制、commit hash）。
3. **正常环境最终验证**：完整 `pytest`（应 348 passed）+ `scripts/smoke_final_project.py` + `scripts/smoke_e2e.py` + `docker compose config --quiet`。
4. **按 AGENTS.md 提交纪律**：compileall → 单测 → 相关 smoke → secrets 审计（`git diff --cached --name-only`）→ 提交（通用描述，如 `fix: harden SSRF/async/read-only boundaries and fix CI`）→ 推送 `origin/feature/improvements`。**不要**提交 `TASK.md`、`CLAUDE.md`、`docs/`、`.env`。

## 五、改动文件清单

**新增（未跟踪）**：
- `app/tools/ssrf.py`
- `requirements-dev.txt`

**修改（已跟踪，未暂存）**：
- `.github/workflows/ci.yml`
- `app/agent/executor.py`
- `app/agent/file_access_policy.py`
- `app/api/tasks.py`
- `app/api/tools.py`
- `app/config.py`
- `app/mcp/policy.py`
- `app/security/redaction.py`
- `app/tools/base.py`
- `app/tools/defaults.py`
- `app/tools/file_reader.py`
- `app/tools/pdf_reader.py`
- `app/tools/registry.py`
- `app/tools/sql_query.py`
- `app/tools/sql_safety.py`
- `app/tools/web_fetcher.py`
- `requirements.txt`
- `tests/test_phase7.py`

## 六、验证命令

```powershell
.\.venv\Scripts\python.exe -m compileall -q app scripts frontend migrations tests
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts\smoke_final_project.py
.\.venv\Scripts\python.exe scripts\smoke_e2e.py
.\.venv\Scripts\python.exe scripts\skill_smoke.py --all
docker compose config --quiet
```

## 七、重要注意事项 / 陷阱

1. **`TASK.md` 编码已修复**：原为「前 127189 字节 UTF-8 + 末尾 911 字节 GBK」混合编码，已无损转为纯 UTF-8；read 工具现在可正常读取。全仓库 209 个文本文件现均为合法 UTF-8。
2. **沙箱限制**（DSH 环境，非代码 bug）：① 给原生命令加 `|`/`>` 管道重定向会被拦（`Access is denied`）；② `subprocess.run` 的 `CreatePipe` 被拦（smoke_final_project 因此失败）；③ `tempfile`/`mkdtemp` 写入被拦（导致 34+5 个测试在此环境失败）。请用无管道直连运行命令，或在正常环境验证。
3. **`git ls-files` 在此沙箱返回空**（伪象）；`git status` 正常，`git cat-file -e HEAD:<path>` 正常。迁移文件在 `migrations/versions/` 子目录（标准 Alembic 布局），完整无缺。
4. **local-only 文件不提交**：`CLAUDE.md`、`TASK.md`、`docs/`、`.env`、workspace 运行产物。
5. 本项目约束见 `AGENTS.md`：工具只读、失败必须可见、不引入 RAG/tenant 隔离等。
