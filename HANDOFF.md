# Traceable Research Agent Handoff

更新时间：2026-08-06

## 1. 现在正在做什么

- 当前步骤是为下一位 Agent 准备可直接执行的交接信息；没有正在进行的代码修改或合并操作。
- 当前检出分支是 `main`；合并基线 `e6f2ffd` 已同步到 `origin/main` 和 `origin/feature/improvements`。
- 创建本文件前工作区干净：`git status -sb` 输出 `## main...origin/main`；当前唯一新增内容是本交接文件。
- Docker Compose 服务仍在运行，API 为 `healthy`，Streamlit 已监听 `8501`。
- 创建本文件是为了保留最新验证结果、唯一已确认的 CI 缺口和后续操作顺序，避免下一位 Agent 重复排查。

## 2. 已经完成了什么

### 代码与公开文档

- Phase 7 收尾修复已合并到 `main`：
  - 计划审批往返保留完整 `raw_step`，后端拒绝空计划、未知/重复步骤、工具篡改和断开的 `arguments_from` 依赖。
  - 普通执行和计划审批都持久化 `memory_recall` trace；异步入口不会绕过 `waiting_human_plan`。
  - 引用按唯一 citation label 校验，支持中文 token/全角句界，可选 LLM 二次判定，指标通过迁移 `0008_citation_validation_metrics` 持久化到 `agent_runs`。
  - 新增 `tests/test_phase7.py`，覆盖审批、依赖拒绝、取消、SSE、memory trace、引用去重、中文判定、LLM 判定和持久化。
- 根目录 README 已按 CSV 格式重写并新增中文版本：
  - `README.md`
  - `README_zh.md`
- README 提供 Docker 快速开始、离线本地审计 Demo、真实调研脚本、配置/API/架构/安全/质量/路线图/贡献/许可证说明。
- 当前仓库没有根目录 `LICENSE`；README 已如实标注，未擅自选择许可证。

### Git 状态

- `feature/improvements` 已快进合并到 `main`，没有创建额外 merge commit。
- 合并命令：`git merge --ff-only feature/improvements`
- 推送命令：`git push origin main`
- 最终提交：`e6f2ffd docs: refresh bilingual readmes`
- 远端推送成功；随后本地状态为 `main...origin/main`。

### 已执行验证

- `.venv\Scripts\python.exe -m compileall -q app scripts frontend migrations tests`：通过。
- `.venv\Scripts\python.exe -m pytest -q`：`205 passed, 15 subtests passed`。
- `scripts\smoke_final_project.py`：`18/18` 通过。
- `scripts\run_eval_regression.py`：`25 passed, 0 hard failed, 3 network skipped`。
- `scripts\skill_smoke.py --all`：`4/4` 通过。
- `docker compose build --pull`：API 与 Streamlit 镜像构建成功。
- `docker compose up -d`：API/Streamlit 容器启动成功；`/health` 返回 `status=ok`，Streamlit `http://127.0.0.1:8501` 返回 HTTP 200。
- `docker compose exec -T api python -m pytest -q`：`205 passed, 15 subtests passed`。
- `docker compose exec -T api python -m alembic current`：`0008_citation_validation_metrics (head)`。
- 容器内任务 `511ba40df45c4370bb02d5421169d8cf` 完成 `waiting_human_plan -> approved -> completed`；报告 HTTP 200；持久化了 `memory_recall`、`plan_approval`、`file_reader`、`sql_query` 和 `citation_validator` trace；`citation_total=3`、`citation_accuracy=0.6667`。
- `docker compose ps` 当前显示：API `healthy`，Streamlit `Up`，端口分别为 `8000` 和 `8501`。

## 3. 卡在了哪里

- 当前没有 Git 合并冲突或本地工作区阻塞；`origin/main` 是当前提交的祖先，已完成快进合并。
- 已确认的未完成事项是 CI 配置漂移：
  - `.github/workflows/ci.yml:32` 仍执行 `python -m pip install -r requirements-docker-light.txt`。
  - 仓库根目录的 `requirements-docker-light.txt` 已不存在，`Test-Path requirements-docker-light.txt` 返回 `False`。
  - 因此不能把本地 pytest 通过误认为 GitHub Actions CI 一定能启动；下一位 Agent 需要先修复该契约。
- 当前没有许可证文件；这是公开再分发前的法律/发布缺口，不应由 Agent 擅自决定许可证类型。
- 之前 `gh auth status` 显示 GitHub CLI token 无效；本次没有创建 PR，也不需要 PR 才能完成已授权的直接合并。若未来要创建 PR，先运行 `gh auth login -h github.com`。
- 曾有两次工具层环境问题：`git merge-tree --write-tree` 因尝试写入 `.git/objects` 被权限拒绝；第一次 `git push origin main` 因 TLS 握手失败，重试后成功。这两次都不是代码冲突。

## 4. 下一步做什么

1. 修复 CI 依赖漂移：检查 `requirements.txt` 与 Docker 镜像依赖，选择恢复 `requirements-docker-light.txt` 或将 `.github/workflows/ci.yml:32` 改为现有依赖文件；然后运行 CI 同等命令：`python -m compileall app scripts frontend tests`、`python -m pytest tests` 和 workflow 中的 smoke 脚本。
2. 检查并决定许可证：由项目负责人选择明确许可证，新增根目录 `LICENSE`，再同步修改 `README.md` 和 `README_zh.md` 的 License/许可证章节。
3. 在 `main` 上重新执行发布前验证：`python -m compileall -q app scripts frontend migrations tests`、`python -m pytest -q`、`python scripts/smoke_final_project.py`、`python scripts/run_eval_regression.py`、`python scripts/skill_smoke.py --all`、`docker compose config --quiet`。
4. 若要复核运行时闭环，启动或检查 `docker compose up -d`，访问 `http://127.0.0.1:8000/health` 和 `http://127.0.0.1:8501`，再用 `local_audit` + `require_plan_approval=true` 验证审批、trace、报告和引用指标。
5. 若验证完成且不再需要本地服务，执行 `docker compose down`；不要删除 `workspace/`，其中包含本地数据库和运行产物。

## 5. 哪些坑不要再踩了

- 不要暂存或提交 `TASK.md`、`CLAUDE.md`、`docs/`、`.env`、数据库、缓存、报告和 `workspace/eval_outputs/`；它们是本地专用或生成内容。
- 不要使用 `git merge-tree --write-tree` 做只读冲突检查；该模式会写 `.git/objects`。使用 `BASE=$(git merge-base HEAD origin/main)` 后执行 `git merge-tree --trivial-merge $BASE origin/main HEAD`，再检查 `changed in both` 等冲突标记。
- 不要继续引用旧的“Docker Engine 无权限”结论；当前 `docker info`、镜像构建、容器启动、健康检查和容器内测试都已实际通过。
- 不要把第一次 TLS 推送失败当成合并失败；当时重试 `git push origin main` 已成功，当前本地状态为 `main...origin/main`。
- 不要因为本地 pytest 通过就跳过 CI 文件检查；当前 workflow 明确引用了不存在的 `requirements-docker-light.txt`，必须先处理该问题。
- 不要在没有用户授权的情况下选择许可证或宣称项目已具备开源再分发许可。
