# Traceable Research Agent

Traceable Research Agent 是一个面向商业调研、技术选型和客户/竞品情报整理的可追踪调研系统。它不是只返回一段黑盒答案的聊天机器人，而是把一次真实调研任务拆成可检查的执行链路：

```text
任务输入 -> Planner 生成计划 -> 工具执行 -> Trace 记录 -> Evidence 聚合 -> Markdown / Word / PDF 报告
```

项目默认可以离线运行，并通过显式运行模式避免因本机存在 API Key 而隐式联网；配置 Provider 后可接入真实 LLM、GitHub Public API、Tavily Search、Firecrawl/Exa Source Pack 和真实 RAG 后端。当前主交互界面是 Streamlit，后端由 FastAPI 提供 API，MCP 端点用于外部只读工具发现与调用演示。

## 项目定位

很多 Agent demo 能“给出答案”，但难以解释答案来自哪里、哪些工具成功或失败、哪些结果是 fallback、哪些外部调用超时或缺少凭证。Traceable Research Agent 的核心目标是把原本需要运营、产品、销售或技术负责人手动搜索、复制、比对、整理来源的调研任务，变成一个可复跑、可审计、可交付报告的 Agent 工作流。

它适合用于：

- **竞品与市场情报**：调研竞品官网、定价页、文档、更新日志和公开评价，输出带来源链接的对比报告。
- **技术选型与供应商评估**：比较 API 平台、RAG 框架、MCP 工具、数据库或云服务，保留每条结论的出处、限制和风险。
- **销售/BD 会前研究**：围绕目标公司、产品、近期动态和潜在痛点生成 briefing，减少人工资料搜集时间。
- **产品/运营专题调研**：追踪某个新功能、行业趋势、用户反馈或政策变化，把公开网页与内部知识库证据合并。
- **内部知识库 + 外部网页混合调研**：同时读取本地文档、SQL 指标、RAG 语料和网页来源，形成可复核报告。

商业价值不在于“多接了几个工具”，而在于每个结论都能回到不可变证据链：谁调用了什么工具、用了什么输入、拿到了哪个原文片段、来源是否独立可靠、哪里失败或降级、冲突如何处理以及最终报告为什么可信。

## 典型落地场景

| 场景 | 用户问题示例 | 调用链路 | 交付物 |
| --- | --- | --- | --- |
| 竞品分析 | “调研三家 AI API 网关的定价、模型支持、限流和文档成熟度。” | Tavily/Exa 发现来源 -> Firecrawl 读取页面 -> Evidence 聚合 -> 报告 | 竞品对比 Markdown / Word / PDF |
| 技术选型 | “比较 FastAPI、LangGraph、MCP SDK 在 Agent 工具编排中的适用边界。” | GitHub/文档搜索 -> RAG 本地笔记 -> trace 审计 | 技术选型建议和风险表 |
| 客户研究 | “为某家 SaaS 公司准备售前会前 briefing，找业务线、产品动态和可切入痛点。” | 外部搜索 -> URL 正文读取 -> 结构化摘要 | 会前 briefing 和来源列表 |
| 内部复盘 | “结合本地实验记录、SQL 指标和外部资料，复盘某次 RAG 方案效果。” | file_reader -> sql_query -> rag_search -> report_writer | 可追踪复盘报告 |
| 合规/审计型调研 | “生成报告时标出哪些结论来自 mock/fallback/失败工具，避免把不确定信息当事实。” | Trace -> EvidenceBundle -> hallucination risk / limitation | 带风险声明的审计报告 |

## 核心功能

### 可追踪任务执行

- `POST /api/tasks` 创建任务并持久化计划。
- `GET /api/tasks/{run_id}/plan` 查看 Planner 生成的结构化步骤。
- `POST /api/tasks/{run_id}/run` 或 `/run_async` 执行任务。
- `GET /api/tasks/{run_id}/trace` 查看完整 ToolTrace。
- `GET /api/tasks/{run_id}/events` 通过 SSE 查看实时事件。

### Planner 与执行器

- 确定性 Planner：默认可离线运行，适合稳定演示。
- LLM Planner：支持 OpenAI-compatible provider，例如 Qwen / DeepSeek；不可用时可降级。
- Plan Guardrails：在计划落库前归一化文件路径、SQL、GitHub、Tavily、RAG 和远端 MCP 参数。
- Planned Executor：按计划顺序执行。
- Parallel Executor：可并行执行安全、互不依赖的只读工具。
- ReAct Executor：支持逐步观察、失败恢复和受限动态决策。
- Reporter 使用显式 `REPORT_GENERATION_MODE=deterministic|llm`；默认 deterministic，即使环境中存在模型凭据也不会隐式发起报告生成调用。

### 工具系统

内置工具通过统一 Tool Registry 注册：

| 工具 | 用途 | 主要边界 |
| --- | --- | --- |
| `file_reader` | 读取本地 demo 文档 | 默认仅允许 `workspace/docs`；白名单外路径进入 HITL |
| `sql_query` | 查询 demo SQLite | 只允许单条 `SELECT` / `WITH` |
| `rag_search` | 本地 RAG 检索 | 支持 dense / BM25 / hybrid |
| `mcp_github_search` | GitHub 只读搜索 | GET-only；token 来自环境变量 |
| `tavily_search` | 外部网页搜索 | 只读；失败进入结构化 trace |
| `report_writer` | 报告生成 | 不对外暴露为写工具，由 Reporter 内部调用 |
| remote MCP tools | Firecrawl / Exa / Context7 等 | 默认只注册 readonly、side-effect-free、无需确认的工具 |

所有工具结果通过统一错误分类和递归脱敏流程，稳定区分超时、限流、鉴权、Provider、非法结果和内部错误，同时保留原始供应商错误类型用于兼容和审计。

### Trace、Evidence 与报告

- ToolTrace 持久化到 SQLite，记录 step、tool、input/output summary、latency、error、metadata。
- EvidenceBundle 从 trace 中提取证据，并标记 source、mock/fallback、failure、remote MCP discovery/support 等类型。
- Claim 级证据图将 `ToolTrace -> SourceDocument -> SourceSnapshot -> EvidencePassage -> EvidenceAssertion -> ResearchClaim -> ClaimEvidenceEdge -> Citation -> ReportClaim` 串成可反向审计的引用链。
- Web、RAG、SQL、GitHub 和本地文件分别保存 URL/字符区间、document/chunk、查询哈希/表列/行标识、repo/commit/path/line 等定位信息。
- 原始工具输出按 SHA-256 进行 gzip 压缩并保存为不可变制品，SQLite 只保留图关系、定位器、哈希和结构化字段。
- 来源策略从权威性、可溯源性、时效性、相关性、独立性和提取完整性六个维度评分，并通过内容哈希、转载识别和同组织聚类避免重复计算独立支持。
- 百分比、数量级、单位、时间范围和极性会先标准化，再将证据标记为 `supports`、`refutes` 或 `contextualizes`；无法解决的冲突保留为 `unresolved` 或 `requires_human`。
- Reporter 基于任务、计划、观察和证据生成报告，只接受持久化的 Citation ID；缺失或编造引用会触发校验失败，未解决冲突会显示在最终回答和限制章节。
- 报告支持 Markdown、Word、PDF 下载。
- Evidence packet 支持 JSON、JSONL、Markdown 导出和预览，并过滤明显密钥字段。

### Streamlit Demo UI

Streamlit 提供一个轻量研究控制台：

- 左侧控制栏：后端连接、场景模板、执行模式、会话信息。
- 主区流程条：任务描述、执行计划、执行追踪、研究报告。
- 任务页：输入任务、创建任务、执行任务、查看状态摘要。
- Trace 页：实时事件流、工具调用时间线、错误和元数据。
- 报告页：渲染报告、下载 Markdown/Word/PDF、导出 Evidence packet。

### MCP 与 Deep Research Source Pack

项目提供只读 MCP-compatible server，也提供本地 Source Pack Bridge：

- 本项目 MCP server：让外部 Agent 发现只读工具、读取 trace 和报告。
- Source Pack Bridge：把 Firecrawl、Exa、Context7 适配成本项目可注册的 HTTP JSON-RPC MCP 工具。
- 深度网页调研模板：Tavily/Exa 负责发现来源，Firecrawl 负责搜索、map 和正文读取。
- 技术文档调研模板：GitHub、Context7、Exa、Firecrawl 组合使用；未配置远端工具时保持可降级。

## 架构

```text
Streamlit UI / API Client / External MCP Client
                    |
                    v
              FastAPI API Layer
                    |
                    v
                 Planner
      deterministic / LLM / guardrails
                    |
                    v
                Executor
       planned / parallel / ReAct
                    |
                    v
              Tool Registry
 local tools + remote MCP readonly tools
                    |
                    v
     Trace Store -> Claim Provenance Graph
                         |
                         v
         Reliability / Conflict Reasoning
                         |
                         v
                      Reporter
                    |
                    v
   Markdown report / DOCX / PDF / Evidence export
```

## 目录结构

```text
app/
  api/                  FastAPI routers: tasks, reports, tools, events
  agent/                Planner, executors, reporter, evidence, guardrails
  evidence/             Provenance graph, immutable artifacts, reliability and conflicts
  llm/                  LLM provider abstraction and planner client
  mcp/                  MCP server, remote client, channel policy
  mcp_bridge/           Firecrawl / Exa / Context7 Source Pack bridge
  rag/                  Chunking, embeddings, BM25, vector and hybrid retrieval
  security/             API key and tenant/user request context
  tools/                Tool Registry and local tool implementations
  trace/                SQLAlchemy models, persistence and SSE events
config/                 Versioned source reliability policy
frontend/
  streamlit_app.py      Main Streamlit demo UI
scripts/                Init, RAG build, smoke checks, bridge startup
workspace/docs/         Demo corpus for file_reader and RAG
docs/                   Notes and experiment reports
```

## 快速开始

### 1. 准备环境

```powershell
cd E:\BOSS\traceable-research-agent
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 2. 初始化数据库和 demo 数据

```powershell
.\.venv\Scripts\python.exe scripts\migrate_database.py
.\.venv\Scripts\python.exe scripts\init_demo_db.py
.\.venv\Scripts\python.exe scripts\build_rag_index.py
```

迁移脚本会将新数据库或完整的旧版 Demo 数据库升级到当前 Alembic head，其中包括 Claim 证据链和可靠性/冲突审计表；部分缺失的旧 Schema 会直接拒绝启动，避免错误标记版本。

### 3. 启动 FastAPI

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health | ConvertTo-Json -Depth 10
```

### 4. 启动 Streamlit

```powershell
.\.venv\Scripts\streamlit.exe run frontend/streamlit_app.py --server.port 8501
```

访问：

- Streamlit UI: `http://127.0.0.1:8501`
- FastAPI: `http://127.0.0.1:8000`
- OpenAPI docs: `http://127.0.0.1:8000/docs`

### 一键本地演示

Windows 环境可使用：

```powershell
.\start_traceable_demo.bat
```

该脚本会依次启动：

1. MCP Source Pack Bridge: `http://127.0.0.1:9001`
2. FastAPI backend: `http://127.0.0.1:8000`
3. Streamlit UI: `http://127.0.0.1:8501`

## 配置说明

复制 `.env.example` 为 `.env`，按需填写。默认配置不需要真实 API Key，也能跑通离线 demo 和 smoke。

```powershell
Copy-Item .env.example .env
```

### 基础执行配置

```ini
EXECUTION_MODE=planned
PARALLEL_EXECUTION_ENABLED=false
ASYNC_RUN_ENABLED=true
```

- `EXECUTION_MODE=planned`：稳定默认模式。
- `EXECUTION_MODE=react`：启用 ReAct 动态决策。
- `PARALLEL_EXECUTION_ENABLED=true`：允许 planned 模式并行执行安全只读步骤。

### LLM Planner

```ini
LLM_PLANNER_ENABLED=false
REPORT_GENERATION_MODE=deterministic
LLM_PROVIDER=qwen
LLM_PLANNER_MODE=auto
LLM_MODEL=qwen-plus
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_API_KEY=
DEEPSEEK_API_KEY=
```

`REPORT_GENERATION_MODE=deterministic` 是离线安全默认值；即使进程环境中存在
LLM Key，Reporter 也不会隐式联网，只有显式设置为 `llm` 且配置通过校验后才会调用模型。

默认关闭 LLM Planner，保持离线稳定。开启后，LLM 输出仍会经过 schema 校验和 plan guardrails。

### 证据链与可靠性策略

```ini
EVIDENCE_PIPELINE_VERSION=v2
EVIDENCE_EXTRACTOR_VERSION=v2-rule-1
EVIDENCE_ARTIFACT_ROOT=workspace/artifacts
EVIDENCE_PASSAGE_MAX_CHARS=4000
EVIDENCE_REASONING_ENABLED=true
SOURCE_POLICY_PATH=config/source_policy.v1.json
```

- V2 将 ToolTrace 物化为 Claim 级证据图，并把大正文保存到内容寻址的压缩制品中。
- 来源策略按 Claim 类型配置来源等级、时效、域名、最低可靠性和独立来源数；修改策略内容不需要改核心代码。
- 每次推理保存策略版本、策略哈希、引擎版本、评分分项和调和理由，重复读取不会静默覆盖历史策略结果。
- 回退到 V1 时需要同时设置 `EVIDENCE_PIPELINE_VERSION=v1` 和 `EVIDENCE_REASONING_ENABLED=false`。

### 外部搜索

```ini
GITHUB_TOOL_DEFAULT_MODE=public_api
GITHUB_TOKEN=
GITHUB_PUBLIC_API_FALLBACK_TO_MOCK=false

TAVILY_API_KEY=
TAVILY_FALLBACK_TO_MOCK=false
```

未配置 key 时，工具会按配置返回结构化失败或 fallback，不应让任务 API 直接 500。

### 文件读取和 HITL

```ini
FILE_READER_ALLOWED_ROOTS=workspace/docs
FILE_READER_HITL_OUTSIDE_ALLOWED_ROOTS=true
```

默认只读 `workspace/docs`。当任务引用白名单外文件时，Planner 会标记为 `waiting_human`，批准后也只允许读取该次确认的具体路径。

### RAG 后端

轻量默认：

```ini
RAG_EMBEDDING_BACKEND=deterministic
RAG_VECTOR_BACKEND=json
RAG_REAL_BACKEND_ENABLED=false
RAG_RETRIEVAL_MODE=hybrid
```

真实向量后端：

```ini
RAG_EMBEDDING_BACKEND=sentence_transformers
RAG_VECTOR_BACKEND=chroma
RAG_REAL_BACKEND_ENABLED=true
RAG_MODEL_PATH=E:\Models\bge-small-zh-v1.5
RAG_CHROMA_DIR=workspace/chroma
```

### MCP Source Pack

Bridge 默认配置：

```ini
MCP_BRIDGE_HOST=127.0.0.1
MCP_BRIDGE_PORT=9001
MCP_BRIDGE_ENABLED_PROVIDERS=firecrawl,exa,context7
MCP_BRIDGE_FAKE_MODE=true
MCP_BRIDGE_TIMEOUT_SECONDS=20
MCP_BRIDGE_MAX_RESULTS=20
MCP_BRIDGE_MAX_CONTENT_CHARS=12000
```

真实 Source Pack 需要：

```ini
FIRECRAWL_API_KEY=
FIRECRAWL_BASE_URL=https://api.firecrawl.dev
EXA_API_KEY=
EXA_BASE_URL=https://api.exa.ai
CONTEXT7_API_KEY=
CONTEXT7_BASE_URL=
```

启动 Bridge：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start_mcp_source_pack.ps1 -Mode fake
powershell -ExecutionPolicy Bypass -File scripts/start_mcp_source_pack.ps1 -Mode real -Providers firecrawl,exa
```

让 FastAPI 注册 Source Pack：

```ini
MCP_REMOTE_REGISTRY_ENABLED=true
MCP_CHANNEL_READONLY_SERVERS=[{"name":"source_pack","base_url":"http://127.0.0.1:9001/mcp","transport":"http_json_rpc","timeout_seconds":20,"allowed_tools":["firecrawl.search","firecrawl.scrape","firecrawl.map","firecrawl.extract","exa.web_search_exa","exa.web_fetch_exa","exa.web_search_advanced_exa","context7.resolve-library-id","context7.query-docs"],"blocked_tools":[],"headers_env":{},"channel":"readonly"}]
MCP_REMOTE_REGISTRATION_ATTEMPTS=5
MCP_REMOTE_REGISTRATION_RETRY_SECONDS=1
```

也可以在 FastAPI 启动后刷新远端工具：

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8000/mcp/refresh
```

## Docker

轻量 Docker 默认使用 deterministic/json RAG，不打包本地 embedding 模型：

```powershell
docker compose up --build
```

API 容器启动时会先执行 Alembic 迁移，再按配置初始化 Demo 数据和 RAG 索引，最后启动 Uvicorn。`workspace` 以宿主机目录挂载到 API 和 Streamlit，因此 SQLite、不可变证据制品和生成报告在容器重建后仍然保留。

访问：

- FastAPI health: `http://127.0.0.1:8000/health`
- Streamlit UI: `http://127.0.0.1:8501`

停止：

```powershell
docker compose down
```

真实 RAG Docker override：

```powershell
$env:RAG_MODEL_HOST_PATH="E:/Models/bge-small-zh-v1.5"
docker compose -f docker-compose.yml -f docker-compose.real-rag.yml up --build
```

## 安全边界

- 不提交 `.env`、API Key、GitHub Token、本地模型、SQLite 数据库、RAG 索引、缓存、导出文件或生成报告。
- 运行模式、Provider 凭据、证据版本和互斥配置会在启动期校验，错误配置不会进入任务执行阶段。
- ToolResult、Trace、事件、报告上下文和证据导出使用统一递归脱敏策略，并保留稳定的错误分类用于审计。
- 本地文件读取默认限制在 `workspace/docs`，白名单外路径进入 HITL。
- SQL 只允许只读查询。
- GitHub、Tavily、Firecrawl、Exa 均按只读工具处理。
- MCP readonly channel 只自动注册 `read_only=true`、`side_effect_free=true`、`requires_confirmation=false` 的工具。
- interactive/write channel 不进入默认自动执行链路。
- `headers_env` 只记录环境变量名，不把 header 值写入 health、trace、report 或 evidence export。
- 远端工具失败会变成 failed `ToolResult` 和 evidence，不应让任务 API 直接 500。
- Citation 必须来自持久化证据图并能够反查不可变 Passage、Snapshot 和 ToolTrace，Reporter 不接受模型编造的引用标识。
- 高置信结论必须满足来源可靠性和独立来源门禁；未解决冲突不能被静默改写为确定事实。

## 后续可以继续增强

- 任务级 Research Skills、统一 Policy Engine、预算控制、重复动作检测和 re-planning。
- Evidence quality / verifier、来源权重校准和更细粒度的报告 Claim 对齐。
- 单 Tavily、多原子工具、多工具 + MCP 与完整证据流水线的标准化对照评测。
- Trace replay / run replay、持久任务队列、断点续跑和失败恢复。
- 更完整的真实网页站点展开、动态页面抓取和 Provider fallback 策略。
- PostgreSQL、对象存储、六个月 Trace 生命周期、租户隔离和审计授权。
- 更多 eval cases、报告质量评分、可观测性以及更完善的 PR/CI 自动化。

## License

当前仓库尚未声明开源许可证。公开发布或接受外部贡献前，建议补充 `LICENSE` 文件。
