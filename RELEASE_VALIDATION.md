# 修复发布与验收清单（R0–R12.1）

## R12.1 Scope-first Research Result Governance：发布复核记录

本轮以已发布的 `feature/improvements@8021aff` 为远端基线，并将提交包中的完整
R12.1 原子提交链与该远端历史通过普通 Merge 汇合；没有重排或压缩原提交，也不需要
Force Push。合并后的业务文件树与提交包末端 `5c2f2be` 完全一致。在此基础上修复了
一个只在独立执行时暴露的取消回归测试顺序依赖，并同步 README、发布清单和交接状态。

R12.1 将 Deep Research V2 从 Scope 执行内核收口为 Scope-first Research Result
Runtime：统一 Result Resolver/API、Scope Evidence Identity 与有效计数、跨 Run
Reasoning、最终 Claim／Citation Occurrence、双层完成 Gate、Final-cited Academic
Reference Verification、Scope-aware Improvement，以及 Actor／Synthesizer 分角色
可用性与有界证据上下文均已进入同一结果边界。迁移头为
`0013_research_result_governance`；R13 Coverage／Gap／Python Runtime 未提前实现。

| 验证 | 本轮结果与边界 |
|---|---|
| 后端完整回归 | `798 passed / 2 skipped / 1 xfailed / 102 subtests passed / 0 failed`；15 条第三方弃用警告 |
| 官方离线入口 | 收集 793 项：`790 passed / 2 skipped / 1 xfailed / 0 failed`；Offline network guard 为 0 次外部尝试 |
| 独立回归 | 取消期间工具返回不得覆盖 `cancelled` 状态；脱离全量测试顺序单独通过 |
| 前端 | OpenAPI 契约同步；类型检查、Lint、11 个测试文件／106 项测试、生产构建通过 |
| OpenAPI | 使用正式生成脚本连续生成两次，`web/src/api/schema.d.ts` 保持零差异 |
| 迁移与 Smoke | Fresh `0001→0013`、Existing `0012→0013` 专项覆盖；四项 R12.1 强制 Smoke 通过 |
| 真实环境 | 未配置 Actor／Reporter LLM 与 Tavily，真实 Deep Research Runtime 记为 `external runtime blocked`；不冒充真实验收通过 |
| Docker／浏览器 | 静态配置检查可执行；实际镜像启动、Windows 浏览器与真实 Provider 仍需在具备相应环境时验收 |
| 发布状态 | 精确发布提交以远端 `feature/improvements` HEAD 为准；只有该 HEAD 的 GitHub Actions Green 后才可标记 `R12.1 COMPLETE` |

## R12 Deep Research Engine V2：已发布记录（8021aff）

本轮以已发布的 R10 `feature/improvements@b0edf9c` 为基线，
完成 R12 Engine V2 替换。Deep Profile 现在由持久化 Research Scope／Tree 统一编排，
显式 AgentRun lineage 和数据库关系取代 `plan_json` 权威；节点继续复用 ReAct、只读
工具注册表、恢复、Trace、Evidence Pipeline 与根 Run 共享预算。子 Evidence 不复制
到 Parent，只通过 Scope 投影进入整体 Outcome 与唯一最终报告，并保留
`Citation → Passage → Snapshot → Trace → origin_run_id`。

| 验证 | 本轮结果与边界 |
|---|---|
| R12 专项 | 23 项通过；1 项严格 xfail 属于 R14 长报告上下文缺陷 |
| 后端完整离线回归 | 收集 717 项：714 通过、2 条件跳过、1 严格 xfail、0 失败；13 条第三方弃用警告；0 次外部访问 |
| 前端 | OpenAPI 契约同步；类型检查、Lint、11 个测试文件／104 项测试、生产构建通过 |
| 迁移 | 0001→0012、幂等升级、Alembic schema check、SQL parser 全部通过 |
| 综合 Smoke | 18/18 通过；研究完整性与 Docker 静态配置 Smoke 通过 |
| 真实环境 | 未运行 Docker、真实模型、搜索、抓取或 Windows 浏览器人工验收 |
| 发布状态 | R12 已发布为 `feature/improvements@8021aff`；`LICENSE` 未处理 |

R12 Definition of Done 已由自动回归覆盖：Deep Dispatcher 只走 V2；Scope／Node 与
lineage 持久化；父子 Evidence 逻辑聚合；Child Evidence 可进入最终报告；Child
Citation 可反查原 Trace；所有节点共享 root budget；Standard／Offline 旧路径无回归；
旧 `run_deepening` 仅为带弃用告警的兼容 wrapper。

## R11 Retrieval & Source Reliability：已发布记录（dc8919a）

本轮以已发布的 `feature/improvements@b0edf9c` 为基线，按 R11–R15 Deep
Research Engine V2 重构方案完成 R11。外部工具名仍为 `web_fetcher`，内部已经
拆为统一 Fetch Contract、HTTP Backend、HTML Extractor、Content Quality Gate、
隔离 Playwright Browser Backend、PDF Adapter、Firecrawl／Exa Remote Adapter 和
Adaptive Router。HTTP 200 的 JavaScript 空壳、Challenge、CAPTCHA、Cookie／登录墙、
软 404／429、付费墙、PDF 原始数据和低质量模板不会被计为有效研究正文。

URL 规范化、Canonical URL 与内容哈希两级去重已经进入抓取与来源队列；最终
URL 级失败分类及各 Backend 尝试记录进入 ToolResult／Trace。请求 URL、最终 URL、
Canonical URL、Provider、提取方式／置信度、Fetch Status、发布时间、重定向链和
Source Identity 会进入 Evidence、SourceDocument 与 SourceSnapshot。Browser 和
Remote Extract 缺失配置只表示可选能力不可用，不会被提升为全系统故障。

| 验证 | 本轮结果与边界 |
|---|---|
| 后端完整离线回归 | 收集 695 项：691 通过、2 条件跳过、2 项严格 xfail、0 失败；7 条第三方弃用警告；0 次外部网络尝试 |
| R11 专项 | Fetch Contract、质量门、HTTP／Browser／PDF／Remote、路由、SSRF、Canonical／Hash 去重、Evidence／Snapshot、配置与 Docker 契约全部通过 |
| 综合 Smoke | 18/18 通过；本地评估 78/80 通过，2 项真实网络依赖按设计跳过，0 硬失败 |
| Docker | 静态配置 Smoke 通过；镜像声明安装 `playwright==1.62.0` 及 Chromium，并为 API 配置 1 GB `/dev/shm`；当前环境没有 Docker CLI，未实际构建／启动 |
| 真实 Fetch | 未执行；已提供受 `--confirm-real-calls --r11-fetch-smoke` 保护的静态／Browser／PDF／已配置 Remote smoke |
| 发布状态 | R11 已发布为 `feature/improvements@dc8919a`；`LICENSE` 按用户要求未处理 |

两项严格 xfail 是在 R11 开始时冻结的后续阶段缺陷：旧 `deepening.py` 排除子 Run
Observation（R12 替换）以及旧单次报告上下文 7000 字符硬截断（R14 分节 Composer
替换）。它们不是 R11 回归失败，也没有在本阶段越层修改。

真实 R11 Fetch 验收命令：

```powershell
.\.venv\Scripts\python.exe scripts\validate_real_runtime.py --confirm-real-calls --r11-fetch-smoke
```

## R10 Real Runtime：已发布记录（b0edf9c）

本轮完成三档 `RESEARCH_PROFILE`、通用 OpenAI-compatible Provider、稳定 LLM
错误分类、显式真实 Runtime Preflight、新建研究页中文环境状态，以及受
`--confirm-real-calls` 保护的真实端到端验收脚本。Deep Profile 默认采用真实
ReAct／LLM／深化链路和 80 工具、64 LLM、200000 Token、1800 秒的宽松安全上限；
这些是根 Run 与子任务共享的最终护栏，不是固定研究策略，显式环境变量仍可覆盖。

正常 `.env.example` 已收敛为最小真实配置且不默认 Fake Mode；`.env.example.full`
保存高级覆盖项，`.env.example.offline` 单独承载 CI、开发
和演示配置。`POST /api/runtime/preflight` 只有在用户显式触发时才调用真实 LLM、
Tavily 与网页抓取。模拟结果不能通过真实预检；返回值、Trace、日志和前端均不包含
API Key、Authorization header、Provider 响应正文或提示词。

Planner 主调用及分解调用、ReAct Actor、Deepening Critic、Planned／ReAct／
Deepening Synthesizer 的失败均写入脱敏 Trace；空响应、非法结构、鉴权／限流／
服务异常与预算耗尽不再被模糊为同一种错误。前端在执行过真实预检后会明确显示
“已连接”或“验证失败”，不会把已失败的探测继续显示为“已配置（未验证）”。

| 验证 | 本轮结果与边界 |
|---|---|
| 后端完整离线回归 | 收集 651 项：649 通过、2 条件跳过、0 失败；7 条第三方弃用警告；0 次外部网络尝试 |
| R10／恢复／完整性／深化专项 | 153 项通过，另有 24 个子测试通过 |
| 前端 | OpenAPI 契约已同步；类型检查、Lint、11 个测试文件／104 项测试和生产构建通过 |
| 综合 Smoke | 18/18 通过；本地评估包含在内，0 硬失败 |
| Docker | 静态配置 Smoke 通过；当前环境无 Docker CLI，未实际构建或启动容器 |
| 真实 Provider | 未调用收费模型或 Tavily；实现已完成，但需在使用者配置密钥后运行真实预检及 `--run-task` 验收 |
| 发布状态 | R10 已发布至 `feature/improvements@b0edf9c`；对应 CI 成功；`LICENSE` 未处理 |

真实验收命令：

```powershell
.\.venv\Scripts\python.exe scripts\validate_real_runtime.py --confirm-real-calls
.\.venv\Scripts\python.exe scripts\validate_real_runtime.py --confirm-real-calls --run-task
```

## R10.0a 研究闭环稳定化：本地待提交记录

本轮以 `feature/improvements@3040641` 为基线，并以 R10–R14 真实 Deep
Research 完整性方案及两次真实失败证据为准。根 Run 触及最终报告预留不再提前
写入终止原因，而是进入质量门和受限报告路径；真实总上限、截止时间、费用与权限
越界仍保持硬失败。中文 Token 不再按 UTF-8 字节计数。

技术比较任务现在生成“产品 × 维度”需求矩阵，研究提示同时包含已抓取证据和
优先待抓取来源；缺失覆盖不能被模型的 `achieved` 文本覆盖。核心搜索／抓取工具
和动态步骤按任务复杂度扩展，但仍受根账本安全上限约束。模糊产品比较默认排除
无明确学术意图的学术检索工具，防止将 Codex 等产品名漂移为宽泛论文关键词。

新建研究页已移除 Planned/ReAct 手动选择，默认不提交执行模式覆盖；检索策略
默认“自动”，不提交 `retrieval_profile`，让后端对技术任务推断
`technical_facts`。完整来源队列继续由 Trace 重建，不再复制进 `plan_json`。

| 验证 | 本轮结果与边界 |
|---|---|
| 后端全量 | 收集 628 项：626 通过、2 条件跳过、0 失败；另有 87 个子测试通过 |
| 前端全量 | 11 个测试文件、103 项测试通过；类型检查、Lint、生产构建通过 |
| 综合 Smoke | 18/18 通过；包含 Planner、E2E、HITL、MCP、Provenance 容量、Reasoning 容量和本地评估 |
| 本地评估 | 80 项中 78 通过、2 项真实网络用例按网络依赖跳过、0 硬失败 |
| Docker | 静态配置 Smoke 通过；当前环境无 Docker CLI，未实际构建或启动容器 |
| 发布状态 | 修改尚未提交或推送；`LICENSE` 未处理 |

官方离线测试入口现在自动使用一次性 SQLite，强制 Offline Profile，并清空所有
已知外部服务密钥后再启动 pytest；子进程继承该隔离环境。这样既避免测试读取、
改写部署 workspace 数据库，也防止本地 `.env` 意外触发真实 Provider。

## 前端执行信息精简与中文提示：已发布记录（3040641）

本轮按页面截图移除计划复核页的工具许可名单、规划备注和执行边界说明，并从
实时工作台移除“共享执行预算”“工具恢复与许可名单”两个内部板块；候选来源与
抓取进度继续保留。历史 Run 中持久化的英文完整性警告会在工作台与报告页转换
为中文。API、Trace、预算和恢复数据本身未删除或改写。

| 验证 | 本轮结果与边界 |
|---|---|
| 前端专项 | 9 项通过，覆盖指定板块隐藏、候选来源保留和旧英文警告中文化 |
| 前端全量 | 11 个测试文件、103 项测试通过；类型检查、Lint、生产构建通过 |
| 隔离页面样本 | 59 项检查通过；不代表真实浏览器布局或真实服务验收 |
| 待验收 | Windows Docker 重建后的桌面浏览器人工核验 |

## R9 后仓库一致性修复：已发布记录

基于 R9 的 `feature/improvements@91b55e5`，仓库一致性修复已形成
`9160a6a`，其功能分支 CI 同步修复为 `adc76a9`。本轮覆盖交接／发布文档、
README 验证数字、功能分支 CI、MCP Skill 契约与参数传递、SQL 描述，以及
DOCX／XLSX 读取能力。`LICENSE` 按要求不处理。移动中的最终状态以
`feature/improvements` HEAD 和对应 CI 为准；下方不覆盖 R9 的已发布历史。

| 项目 | 状态 |
|---|---|
| 文档与 CI | 已同步；`feature/improvements` push 已纳入触发分支，首次运行暴露的 OpenAPI 快照遗漏和 R4 测试环境泄漏已修复 |
| `skill_runner` 元数据与参数 | 非只读／非无副作用契约、参数传递和无效参数拒绝均通过回归与 MCP smoke |
| SQL 描述 | 已与现有 `sqlglot` 实现对齐 |
| DOCX／XLSX | 有界只读解析回归通过；PDF 明确路由到 `pdf_reader`；API 清单新增固定版本 `openpyxl` |
| 完整离线 pytest | 收集 601 项：599 通过、2 条件跳过、0 失败、0 外部网络尝试；有 7 条第三方弃用警告 |
| 前端门禁 | OpenAPI 类型同步、类型检查、Lint、110 项测试、生产构建和 59 项 QA 检查通过 |
| 其他验证 | Python 编译、16 项定向回归、研究完整性、质量评估、Docker 配置、Planner、MCP server/client、Streamlit 与 Provenance smoke 通过；Docker 实际启动、真实浏览器、真实外部服务未运行 |

## R9 目标达成与恢复修复：最新记录

R9 基于 `feature/improvements@6858dda` 实现，并已作为
`feature/improvements@91b55e5` 推送到远端；没有调用收费服务，
没有修改部署历史数据库、上传 Trace 或课程文档。下方 R8 与 R0–R7 为历史记录。

| 问题 | 本批处理 |
|---|---|
| 有正文却未取得数据，仍显示成功 | 明确未完成／工具不可用／动态步骤耗尽不能通过目标检查；股票数据类目标要求实际读取表格，核对指标列、口径、基本日期覆盖与明确给出的代码，不能用财务比率或收盘价冒充涨幅 |
| 相对时间和数据口径漂移 | 应用按任务创建日期固化相对范围并传给 ReAct、深化和报告；缺少日期／间隔／复权口径时阻止审批，提示补充问题，不提示修改密钥 |
| 预算真实原因被报告异常掩盖 | 预算异常穿透报告和深化；子任务触及共享硬上限后父任务停止，不继续合成或通过质量门槛；失败阶段、错误码和预算原因一致 |
| 深化用光最终报告余量 | 新账本预留最多 8000 token／2 次 LLM 调用（分别不超过总量 10%／20%）；仅最终根报告可用，真正子任务不能借用，完整重试拥有新账本；余量不足时跳过可选深化，不保证报告一定生成 |
| 静态步骤挤占动态步骤 | 持久化 ReAct 步骤偏移和上限，升级后的动态额度独立、Trace 序号递增；审批恢复不重置额度 |
| 子任务 `/plan` 500／导出遗漏 | 新子计划显式空步骤；旧 `deepening-v1` 缺步骤只读补齐响应，不写回；先保存父子关联再执行，预算异常也能找到子 Run |
| 已抓取正文仍无法阅读 | 获准 `web_fetcher` 用本 Run 来源 ID 分页读取已持久化正文，不发 HTTP、不复制证据；拒绝模型伪造快照、跨 Run 读取及工具权限扩张 |
| 重复抓取／虚构文件路径 | 混合输入只保留新 URL，并去掉同批重复项；全部已完成时不调用工具。相同完整正文提示改用来源 ID；不支持的 HTML 文件读取在人工确认前阻止；保留独立搜索摘要与未选候选，同层级优先未读 URL；明显模板空壳不计有效全文 |
| 引用与历史统计 | 小数和 URL 不再误截断引用句子；新完整性规则为 `research-integrity-v2`，旧版完成结果只读标记待复核并排除可信趋势，不重写原报告 |
| 任务与界面展示 | 普通列表不再把深化子任务当独立发布任务；仍可直接查看审计，完整重试不隐藏。去掉用户框选的说明文字及“能力”“系统与质量”导航，底层路由与 API 保留 |

数据检查是保守的必要条件，不是普适事实核查：目前只提取有限大小的 CSV／静态
HTML 表格，不执行 JavaScript，不增加金融数据连接器或涨幅计算器；不具备证券
名称到代码的可信解析，也不验证完整交易日历、停牌和公司行动。若问题中有证券
代码则优先在同一来源核对代码（支持代码紧邻中文）；否则只做同页名称匹配，
不宣称已完成证券身份核验。每日／月度／其他间隔分别做
14／62／370 天的基本连续性检查，端点最多容许 7 天缺口，不是交易所级校验。
无法取得足够材料时会失败，不用模拟数据替代。跨子任务证据仍保留原 Run 身份。

| 验证 | 本轮结果 |
|---|---|
| R9 专项 | 34 项通过；含实际本地 CSV 读取 → 目标检查 → 保存报告 → 引用关联 Trace，以及同一收盘价数据不能满足涨幅请求、跨股票错配和重复 URL 的反例 |
| 后端全量 unittest 发现 | 582 项中 570 通过、12 缺 pytest／Streamlit 依赖报错；零断言失败、零外部网络尝试；未运行 pytest 专用函数式用例，不能记为全量通过 |
| 前端 | 110 项测试、类型检查、Lint、生产构建通过；OpenAPI 声明重新生成。仍有测试运行时 React Router／act 提示，不属于浏览器验收 |
| 隔离 API 进程重启 | 旧子计划读取且不改写、问题口径预检阻止调用；既有文件／SQL 报告、会话、记忆、审计、预算与来源重启保留通过 |
| 隔离页面样本 | 59 项路由／隔离检查通过；不代表真实浏览器布局或无障碍验收 |
| 静态 | Python 编译、差异空白检查、环境变量覆盖、Docker／Streamlit 静态检查通过；无新依赖、环境变量或迁移 |
| 仍未验证 | 完整 pytest、Docker 实际构建／启动／容器持久化、Streamlit 实际启动、桌面／390px 浏览器、Figma 核对、真实外部搜索／模型与股票数据质量 |

环境仍无 Docker 与所缺依赖；未重试此前被中止的下载，未绕过浏览器预览访问限制。
可在已安装依赖的隔离环境复现：

```bash
python -m unittest tests.test_r9_goal_recovery
python scripts/run_offline_tests.py --runner pytest
python scripts/smoke_research_integrity.py
```

发布后仍需在用户原预览目录更新并重建 `api web`。本地代码和固定材料回归不能
代替用户部署／真实联网验收；本批不自动发布、不删历史任务、不更改密钥与额度。

## R8.6 页面说明与集成回归：历史记录

本次交付包含 R8.0–R8.6，基于 `5e188cd`，经授权提交至 `feature/improvements`；
实际发布版本以分支提交为准，`main` 不在发布范围内。以下为本地验证记录，未执行
远端 CI、未调用收费服务，未修改部署历史数据库或上传文档。
后面的 R8.3–R8.5、R8.0–R8.2 和 R0–R7 均为历史快照，旧的“待实现”以本节为准。
**可用环境中的回归完成，不代表完整无密钥验收或真实联网验收通过。**

| 页面／接口 | 本批变化 |
|---|---|
| D03 新建、D04 审批 | 解释 GitHub 可选、真实模式禁止转 mock、Planned 与 ReAct 恢复区别；空许可名单不再写成允许注册表全部工具 |
| D05 工作台 | 逐工具禁用／冷却／输入拦截与剩余额度、父子共享预算、费用不可估与未启用上限、停止原因、候选来源进度与精确 Trace 跳转 |
| D06 证据、D07 报告 | 来源摘录单独标识，不宣称综合结论已核实；显示 Snapshot／Trace 身份并保留精确引用跳转 |
| 计划 API | 类型化 `execution_insights` 与 `execution_budget`；来源从权威 Trace 重建，预算读取根账本，许可绑定 Run 原始限制；只读查询不创建旧任务预算、不改历史计划 |
| 隔离走查 | 新增 GitHub 失败后继续、总预算耗尽、旧结果待复核三种样本；只提供固定数据，拒绝写入，不接生产 API |

复用现有 Panel／StatusChip 样式并补长文本换行、可聚焦链接和原生 details。
新增组件没有捏造 Figma 节点映射；样式与 DOM 测试不代替实际浏览器、触控、读屏验收。

| 验证 | 本轮结果与边界 |
|---|---|
| R8.6 后端专项 | 9 项通过：只读、原始权限、真实 Trace 来源、逐工具范围、过期冷却、父子实时计数及 401 恢复后页面契约 |
| R8＋可信性组合 | 109 项通过（9＋25＋34＋41，为全量发现子集，不能重复累加） |
| 后端全量 unittest 发现 | 547 项中 535 通过、12 缺依赖错误；零断言失败、零外部网络尝试；缺 pytest／Streamlit，未执行全部 pytest 函数式用例 |
| 前端 | 108 项通过（新增 15 项 R8），类型检查、Lint、生产构建通过；OpenAPI 声明已生成 |
| 组合研究 | 固定搜索／模型／抓取结果，实际生成和保存报告，验证 GitHub 401 → 非 GitHub URL → 正文 → 引用／片段／Trace → 页面接口；不是实际外网恢复 |
| 页面路径 | DOM 组件测试通过报告 → 精确引用 → 证据片段 → Trace；覆盖读取失败刷新、取消、未知预算、费用及旧接口无字段 |
| 实际本地 API | 缺密钥与权限冲突阻止审批；临时文件／SQL 研究实际生成报告；进程重启后保留任务、Trace、报告、会话、记忆、审计、预算与来源说明 |
| 隔离样本服务 | 59 项检查通过；仅服务路由与隔离测试，不是浏览器截图或视觉验收 |
| 静态 | Python 编译、环境变量覆盖、差异检查、Docker／Streamlit 静态检查通过；脚本输出 `ok` 不等于应用实际启动 |
| 未通过的独立验收门槛 | 完整 pytest、Docker 构建／启动／容器持久化、Streamlit 实际启动、浏览器桌面／390px／原生键盘与读屏、Figma 核对、真实模型／搜索与深化 |

Docker 命令在本环境不可用；pytest／Streamlit 未安装，未再次尝试此前中止的依赖下载。
浏览器本地预览此前被拒绝访问，本批没有绕过该限制或声称视觉验证通过。
没有主动配置 API Key，也没有把固定材料当成真实联网证据。

### 本轮新增复现入口

仓库根目录、已具备依赖的隔离 Python 环境：

```bash
python -m unittest tests.test_r8_execution_view tests.test_r8_context_budget tests.test_r8_recovery tests.test_research_integrity
python scripts/run_offline_tests.py --runner pytest
python scripts/smoke_research_integrity.py
```

完整部署与验收命令仍见下方。发布之后可继续使用原预览目录与 Compose 项目，
但前后端代码都已变化，需要重建 `api web`，不能只 restart 旧镜像。
预算迁移依旧为 `0011_run_budgets`；R8.6 未新增迁移或环境变量。

## R8.3–R8.5 历史验证记录

在 `5e188cd` 与保留的 R8.0–R8.2 未提交改动上继续实施；本批同样未提交或推送。
未调用收费 API、未修改部署历史数据库或上传文档。下方 R8.0–R8.2 是历史快照，
以下记录保留该批完成时的状态；当时 R8.6 尚未开始，当前状态见文件顶部 R8.6 节。

| 阶段 | 本批交付 |
|---|---|
| R8.3 | 从完整 Trace 重建持久化来源队列，保留 URL、标题、片段、来源与 Trace 身份、抓取进度和缺口；队列／提示词限长、域名多样性与敏感 URL 过滤 |
| R8.4 | 原子共享预算账本，父子任务、顺序／并行／ReAct、报告模型共同记账；取消、恢复、重试与预算耗尽保持一致；计划 API 返回实时预算 |
| R8.5 | 报告与证据 API 共用权威 Trace；不按步骤号把计划目标当事实；摘录关联实际片段；重复输出保留不同 Trace 快照；旧 ReAct 错配风险只读标记待复核 |

新增迁移 `0011_run_budgets` 只建预算表，不清库、不批量重算旧证据。已验证从
旧版本升级及二次启动，历史记忆保留。部署更新时须通过正常启动迁移，再运行新版
代码；不要手工删库或跳过迁移。前端 OpenAPI 类型已同步新增计划字段。

预算高级配置见 `.env.example.full`：基础 `standard` 默认总工具调用 40、模型调用 40、记账 token 100000、
总时长 900 秒；子任务不会获得独立的整份额度。完整重试是新 Run／新账本，审批
恢复保留旧计数和截止时间；工具人工确认等待计入总时长。预算范围是执行阶段，
不含草稿规划、独立工具 API 或执行结束后单独发起的记忆提取。

费用上限默认关闭。开启后要求部署方提供保守人民币价格估值，不使用隐式默认
价格填补未知费用。未知价格拦截调用；缺少 usage 时保留输入 UTF-8 字节加输出
额度的保守预留。这不是精确 tokenizer 或服务商账单限额。工具次数不是抓取器
内部每次 HTTP 请求数；截止时间阻止新操作，不强杀已进入网络库的请求。
预算耗尽返回结构化 `budget_exhausted`，保留材料与 Trace，中间报告不冒充最终报告。

| 验证 | 最新结果 |
|---|---|
| R8.3–R8.5 专项 | 25 项通过 |
| R8 新旧＋可信性组合 | 100 项通过（25＋34＋41，均为全量发现子集） |
| 全量 unittest 发现 | 538 项：526 通过、12 项缺 pytest／Streamlit 依赖错误；零断言失败、零外部网络尝试 |
| 前端 | 93 项测试、类型检查、Lint、生产构建通过 |
| 实际报告组合回归 | 固定模型／搜索／抓取替身；实际证据物化、生成并保存 Markdown；引用逐项关联 Passage、Snapshot、Trace，GitHub 401 不阻止其他网页路径 |
| 预算并发／恢复 | 多 Session 争抢额度不超限、重开数据库保留计数、子任务共享、完整重试清零、取消后阻止调用通过 |
| 隔离 API 进程 | 缺密钥／权限冲突阻止审批，本地文件＋SQL 报告、来源映射及预算在重启后保留，通过 |
| 静态与契约 | Python 编译、差异空白检查通过；OpenAPI 类型已重新生成 |
| 仍未验收 | 完整 pytest、Docker／Streamlit 实际启动、浏览器视觉、真实外部模型与搜索 |

报告组合测试不再替换报告生成器，但它仍使用固定外部材料，并非真实联网证据。
摘录可追溯不代表材料本身一定正确，也不代表模型综合结论已经事实核查。
子任务材料保留原 Run 身份，学习笔记不自动变成父报告受支持结论。真实联网与
R8.6 界面验收仍须另行进行，不能宣布整个 R8 或完整无密钥验收结束。

## R8.0–R8.2 历史验证记录

基线 `5e188cd`；本批在该基线上本地实现，未提交、未推送。仅覆盖权限／来源约束
与有界逐工具恢复，不代表 R8 全部完成。未调用真实模型或搜索服务，未修改部署
数据库、用户上传文件或历史报告；无新增环境变量、无数据库迁移。

已实现：默认 Skill 权限补齐必需工具；保留显式限制（含空名单），冲突阻止审批；
执行端绑定 Run 原始来源模式与权限；顺序／并行／ReAct／补充检索拦截真实任务的
mock、offline、fallback 参数和演示结果；深化子任务不扩大父任务权限。
通用深度 Web 的提示词不再强制调用 GitHub／远端 MCP；逐工具恢复状态可追踪、
可恢复，完整重试清空旧状态，取消保持终态。

| 情况 | 本批处理 |
|---|---|
| GitHub／搜索认证失败（401） | 本 Run 禁用相应提供方，其他获准工具可继续 |
| 403 权限不足 | 拦截同一输入；不误当作所有 403 都限流 |
| 403 带限流信号、429 | 冷却；读取 Retry-After，避免立即重复调用 |
| 暂时性提供方错误／超时 | 有界冷却，在剩余调用次数内重新选择路径 |
| 单页抓取失败、无效输入、空搜索结果 | 拦截相同输入，其他 URL／查询仍可使用 |
| 单工具调用上限 | 仅该工具不可用，不直接结束整条研究 |
| 实际无可行工具、总步骤耗尽 | 进入最终证据检查；不足则失败，不用 mock 补齐 |

限制：恢复调度当前在 ReAct；顺序／并行保留既有计划调度但共用权限与来源守卫。
GitHub／Tavily 的 ReAct 内层 HTTP 重试关闭，由外层恢复管理；现有
`REACT_SAME_TOOL_MAX_CALLS` 与 `REACT_MAX_STEPS` 仍生效。被拒绝的选择消耗步骤但
不算实际工具执行。没有新增全局时间／费用预算，也没有跨深化子任务共享预算；
冷却期间会选择其他工具，不新增后台等待调度器。禁止同一输入基于参数摘要，
尚不是跨批次 URL 队列或逐域名调度。R8.3–R8.6 的上下文、预算、证据关联、界面
工作仍未实施；不能将这批称作原失败任务的完整真实联网修复验收。

| 验证 | 结果与边界 |
|---|---|
| R8 专项 | 34 项通过；HTTP、模型决策、搜索与网页材料均为固定测试替身 |
| R8＋既有可信性组合 | 75 项通过，是全量测试子集，不另行相加 |
| 全量 unittest 发现 | 513 项：501 通过、12 项依赖导入错误；零断言失败、零外部联网尝试 |
| 完整 pytest | 未运行；缺 pytest，另缺 Streamlit；unittest 不覆盖全部函数式 pytest 测试 |
| 前端 | 93 项测试、类型检查、Lint、生产构建通过 |
| 隔离真实 API 进程 | 缺密钥及缺获准抓取能力阻止审批；本地文件／SQL 有效证据与报告通过；重启后数据保留 |
| 编译与差异检查 | Python compileall、git diff --check 通过 |
| Docker、浏览器、真实外部研究 | 本批未验证；原验收缺口保留 |

“GitHub 401 → 其他搜索 → 非 GitHub 抓取”的专项验证了真实模式控制流程与有效
材料计数，但报告生成器使用测试替身，并未证明真实模型会自主选对 URL，或最终
报告的引用／结论关联正确。上述证据错配须在后续批次修复后组合验收。
本地／SQL API 冒烟则实际生成临时报告，测试数据与部署数据隔离。

## R0–R7 验证快照与部署指令

当前状态：本地修复与可用回归已完成，**尚未完成全部无密钥验收，更未完成真实联网验收**。
本文件不是“已部署”或“已推送”的声明。提交、推送、调用真实外部服务需分别授权。

### R7 已核实与未核实（历史快照，最新数字见上方 R8）

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
| D03 新建 | 配置状态、输入保留、存储禁用提示、R8 执行边界 | 键盘、草稿与会话隔离 |
| D04 审批 | 预检、恢复、冲突同步、重复操作保护、原始权限说明 | 按钮与长计划布局 |
| D05 工作台 | Trace、人工确认、取消、重试、R8 共享预算／恢复／候选来源 | 真实 SSE 断线／刷新与 Nginx 转发 |
| D06 证据 | 摘要／全文、精确关联、摘录来源与 Snapshot／Trace、导出 | 引用到来源的连续操作 |
| D07 报告 | 安全渲染、下载、缺失／失败状态、摘录与核查边界 | 长表格、代码与引用跳转 |
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
6. 执行下方命令。R4–R8 修改了前端，所以本次需要重新构建 api 与 web 两个镜像。

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
