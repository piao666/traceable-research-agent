# R0–R7 发布与验收清单

当前状态：本地修复与可用回归已完成，**尚未完成全部无密钥验收，更未完成真实联网验收**。
本文件不是“已部署”或“已推送”的声明。提交、推送、调用真实外部服务需分别授权。

## 已核实与未核实

| 项目 | 本轮结果 | 边界 |
|---|---|---|
| 前端测试 | 93 项通过 | 包含 21 项 R6 状态／焦点测试与 11 对文字颜色检查 |
| 类型、Lint、生产构建 | 通过 | 不等于浏览器视觉验收 |
| 生成式 OpenAPI 类型 | 重新生成并与前端声明一致 | CI 增加差异检查，远端 CI 尚未执行 |
| 离线 unittest 发现 | 479 项：467 通过，12 项依赖导入错误 | 缺 pytest、Streamlit；函数式 pytest 测试未全部执行 |
| R4／R5／可信性／部署专项 | 88 项通过 | 是全量发现中的子集，不能相加当作独立总数 |
| Python 编译、环境变量覆盖 | 通过 | 编译不能代替运行 |
| 隔离 API | 缺密钥阻塞、本地文件＋SQL 成功研究、有效证据、报告通过 | 只使用临时目录与固定本地材料 |
| API 进程重启 | 草稿、完成 Run、Trace、报告、会话、记忆、审计保留 | 不是 Docker 容器重启验证 |
| 走查样本服务 | 52 项隔离检查通过 | 拒绝所有写入，不是浏览器测试 |
| Docker／Streamlit 静态检查 | 通过 | Docker 不存在、Streamlit 未安装，真实启动未验证 |
| 桌面与 390px、原生弹窗、键盘、读屏 | 未验证 | 浏览器预览被拒绝，未绕过限制 |
| Figma 对照／Code Connect 发布 | 未验证 | 原设计节点不可读；没有生成虚假映射 |
| 普通 Web、ReAct、多轮深化真实运行 | 未验证 | 尚未配置密钥并授权联网验收 |

R7 发现的未模拟文献查询已改用测试替身；抓取、PDF 与 SSRF 测试的 DNS 也使用
固定结果。新的离线入口在 Python 进程内阻止非回环 DNS／连接；任何意外尝试都会
令检查失败，即使应用捕获了异常。最终检查记录为零次外部网络尝试。
这不是操作系统级沙箱，不覆盖任意子进程网络；现有子进程用例仍需保持固定本地输入。

## 页面完成度

| 页面 | 代码／接口状态 | 仍需人工验收 |
|---|---|---|
| D01 概览 | 独立加载、失败重试、未知值与零值区分 | 窄屏与真实空库 |
| D02 任务 | 服务端筛选／分页、键盘 Tab、真实工作台入口 | 长任务名、手机操作 |
| D03 新建 | 配置状态、输入保留、存储禁用提示 | 键盘、草稿与会话隔离 |
| D04 审批 | 预检、恢复、冲突同步、重复操作保护 | 按钮与长计划布局 |
| D05 工作台 | Trace、状态、人工确认、取消、完整重试 | 真实 SSE 断线／刷新与 Nginx 转发 |
| D06 证据 | 摘要／全文、精确关联、来源与导出 | 引用到来源的连续操作 |
| D07 报告 | 安全渲染、下载、缺失／失败状态 | 长表格、代码与引用跳转 |
| D08 会话 | 创建、重命名、历史轮次、关联任务 | 同会话后续研究 |
| D09 记忆 | 确认／拒绝／删除、过期、审计 | 弹窗焦点与删除范围确认 |
| D10 能力 | 真实工具／Skill 清单、可选 MCP 就绪状态 | 无 MCP 时的说明 |
| D11 系统与质量 | 本地诊断、可信质量与单任务详情 | 未评估／旧数据口径 |

## 无密钥开发验证

在已安装仓库依赖的 Python 环境、Node 环境中执行。若缺依赖，先由部署者安装
`requirements.txt` 与 `web/package-lock.json` 对应依赖；不把缺依赖记为测试通过。
使用临时测试环境，不对历史数据运行老的初始化／演示脚本。

```bash
python -m compileall -q app scripts frontend migrations tests
python scripts/run_offline_tests.py --runner pytest
python scripts/smoke_research_integrity.py
python scripts/smoke_docker_config.py
python scripts/smoke_streamlit_frontend.py
python scripts/check_env_vars.py
cd web
npm run generate:api
npm run typecheck
npm run lint
npm test
npm run build
node qa/smoke.mjs
```

`--runner unittest` 是缺 pytest 时的有限检查方式，不能替代 pytest。
静态 Docker／Streamlit 脚本名称中虽有 smoke，但不代表实际启动成功。
GitHub 工作流新增前端／类型同步和隔离 API 检查；本轮没有发布或执行远端工作流。

## 本地部署升级（待发布后执行）

1. 确认目标为预览部署目录及 `feature/improvements`；有未提交修改时停止，不强制重置。
2. 停止该目录的服务：`docker compose stop`。不要停止无关项目，不使用 `down -v`。
3. 完整备份该目录的 `workspace` 与 `.env` 至新目录，包括可能存在的 SQLite WAL／SHM。
   不要只复制运行中数据库的主文件。自定义 `TRACE_DATABASE_PATH` 若位于 workspace 外，
   必须单独备份；Docker 容器内路径需要有对应持久化挂载。
4. 经授权发布后，`git fetch origin` 并快进到发布提交；检查 `git log -1 --oneline`。
5. 在**该部署目录**配置 `.env`，不只修改原来的另一个仓库目录。
6. 执行下方命令。R4–R7 修改了前端，所以本次需要重新构建 api 与 web 两个镜像。

```powershell
docker compose config --quiet
if ($LASTEXITCODE -ne 0) { throw "Compose 配置检查失败" }
docker compose --progress plain build api web
if ($LASTEXITCODE -ne 0) { throw "镜像构建失败；停止并保存脱敏日志" }
docker compose up -d --no-build api web
if ($LASTEXITCODE -ne 0) { throw "服务启动失败" }
docker compose ps
```

不要输出完整 `docker compose config` 或容器环境变量，里面可能有密钥。
默认端口映射可能暴露到主机网卡；`AUTH_ENABLED=false` 仅用于可信本地环境，
不要直接暴露到公网。可用本地 Compose override 将端口绑定至 127.0.0.1，
并先验证合并后的端口配置。浏览器端不提供密钥管理或登录界面。

`init_demo_db.py` 现在只为不存在的 demo.sqlite 创建数据；重启不再删除、重建已有表。
已有库缺表或损坏时会保持原样，需要人工检查；不得用删库“修复”。
迁移失败时保留日志和备份，不自动降级数据库。回退时停止该项目服务，使用匹配的
旧代码及完整备份，在独立恢复目录验证；不要直接覆盖正在运行的数据目录。

## 容器与页面验收

- 服务健康后分别检查 web 的 `/health`、`/api/runtime/diagnostics` 和 API `/docs`；
  前者服务存活不代表搜索或模型连通。经 web 端口访问可同时检查 Nginx 代理。
- 无密钥：历史数据和本地模块可打开，Web 执行明确阻止，本地文件／SQL 任务可运行。
- 创建一条可辨识的本地测试 Run／会话，记录 Trace、报告及已有记忆／审计数量。
  `docker compose restart api web` 后逐项复查，不以“容器 Up”代替持久化验收。
- 如使用 Streamlit，另行构建并启动：`docker compose up --build -d streamlit`；
  检查 8501 页面及原有文件／SQL／Trace／报告操作。
- 桌面与 390px 的详细清单位于 `web/qa/README.md`。固定样本只验证页面，真实 API
  关键路径仍需在部署环境重做，尤其是审批、取消、重试、SSE 与报告下载。

## 最终联网验收（需配置密钥并另行授权）

在实际部署目录设置所用搜索与模型提供方的变量；不要将密钥发到对话或提交仓库。
仅改 `.env` 后需重建容器配置：`docker compose up -d --force-recreate api`，
单纯 restart 不会更新容器环境变量；密钥变化无需重建镜像。

先运行普通 Web 研究，再运行明确选择 ReAct 并打开 `DEEP_RESEARCH_ENABLED=true`
的研究；“深度 Web 模板”本身不保证多轮深化启用。逐项检查真实 URL、网页正文、
模型调用与深化 Trace、有效来源、结论引用及可获得的费用信息。
配置存在、生成 Markdown、显示 completed，都不能单独代表研究合格。
外部错误用受控替身测试，避免为了制造 429 而大量请求真实服务。

通过以上剩余检查后，才能分别宣告“完整无密钥验收通过”与“真实联网研究闭环通过”。
