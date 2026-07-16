# Traceable Research Agent

Traceable Research Agent 是一个面向商业调研、技术选型和客户/竞品情报整理的可追踪调研系统。它不是只返回一段黑盒答案的聊天机器人，而是把一次真实调研任务拆成可检查的执行链路：

```text
任务输入 -> Planner 生成计划 -> 工具执行 -> Trace 记录 -> Evidence 聚合 -> Markdown / Word / PDF 报告
```

项目默认可以离线运行；配置 API Key 后可接入真实 LLM、GitHub Public API、Tavily Search、Firecrawl/Exa Source Pack 和真实 RAG 后端。当前主交互界面是 Streamlit，后端由 FastAPI 提供 API，MCP 端点用于外部只读工具发现与调用演示。

## 项目定位

很多 Agent demo 能“给出答案”，但难以解释答案来自哪里、哪些工具成功或失败、哪些结果是 fallback、哪些外部调用超时或缺少凭证。Traceable Research Agent 的核心目标是把原本需要运营、产品、销售或技术负责人手动搜索、复制、比对、整理来源的调研任务，变成一个可复跑、可审计、可交付报告的 Agent 工作流。

它适合用于：

- **竞品与市场情报**：调研竞品官网、定价页、文档、更新日志和公开评价，输出带来源链接的对比报告。
- **技术选型与供应商评估**：比较 API 平台、RAG 框架、MCP 工具、数据库或云服务，保留每条结论的出处、限制和风险。
- **销售/BD 会前研究**：围绕目标公司、产品、近期动态和潜在痛点生成 briefing，减少人工资料搜集时间。
- **产品/运营专题调研**：追踪某个新功能、行业趋势、用户反馈或政策变化，把公开网页与内部知识库证据合并。
- **内部知识库 + 外部网页混合调研**：同时读取本地文档、SQL 指标、RAG 语料和网页来源，形成可复核报告。

商业价值不在于“多接了几个工具”，而在于每个结论都能回到证据链：谁调用了什么工具、用了什么输入、拿到了什么来源、哪里失败或降级、最终报告为什么可信。

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

### Trace、Evidence 与报告

- ToolTrace 持久化到 SQLite，记录 step、tool、input/output summary、latency、error、metadata。
- EvidenceBundle 从 trace 中提取证据，并标记 source、mock/fallback、failure、remote MCP discovery/support 等类型。
- Reporter 基于任务、计划、观察和证据生成 Markdown 调研报告。
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
     Trace Store -> EvidenceBundle -> Reporter
                    |
                    v
   Markdown report / DOCX / PDF / Evidence export
```

## 目录结构

```text
app/
  api/                  FastAPI routers: tasks, reports, tools, events
  agent/                Planner, executors, reporter, evidence, guardrails
  llm/                  LLM provider abstraction and planner client
  mcp/                  MCP server, remote client, channel policy
  mcp_bridge/           Firecrawl / Exa / Context7 Source Pack bridge
  rag/                  Chunking, embeddings, BM25, vector and hybrid retrieval
  security/             API key and tenant/user request context
  tools/                Tool Registry and local tool implementations
  trace/                SQLAlchemy models, persistence and SSE events
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

### 2. 初始化 demo 数据

```powershell
.\.venv\Scripts\python.exe scripts\init_demo_db.py
.\.venv\Scripts\python.exe scripts\build_rag_index.py
```

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

## API 示例

创建任务：

```powershell
$body = @{
  task = "读取本地文档，查询数据库指标，检索 RAG 证据，并生成中文调研报告"
  report_type = "markdown"
  source_mode = "mock"
  allowed_tools = @("file_reader", "sql_query", "rag_search", "report_writer")
  execution_mode_override = "planned"
} | ConvertTo-Json

$created = Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/tasks `
  -ContentType "application/json" `
  -Body $body
```

查看计划并执行：

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/api/tasks/$($created.run_id)/plan"
Invoke-RestMethod -Method Post "http://127.0.0.1:8000/api/tasks/$($created.run_id)/run"
```

读取 trace 和报告：

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/api/tasks/$($created.run_id)/trace"
Invoke-RestMethod "http://127.0.0.1:8000/api/reports/$($created.run_id)"
```

下载报告：

```powershell
Invoke-WebRequest `
  -Uri "http://127.0.0.1:8000/api/reports/$($created.run_id)/download?format=pdf" `
  -OutFile "research_report.pdf"
```

常用端点：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/health` | 服务、配置和安全状态摘要 |
| `POST` | `/api/tasks` | 创建任务并生成计划 |
| `GET` | `/api/tasks/{run_id}` | 查询任务状态 |
| `GET` | `/api/tasks/{run_id}/plan` | 查看执行计划 |
| `POST` | `/api/tasks/{run_id}/run` | 同步执行 |
| `POST` | `/api/tasks/{run_id}/run_async` | 后台执行 |
| `POST` | `/api/tasks/{run_id}/confirm` | HITL 确认或拒绝 |
| `GET` | `/api/tasks/{run_id}/trace` | 查看工具调用 trace |
| `GET` | `/api/tasks/{run_id}/events` | SSE 实时事件 |
| `GET` | `/api/tasks/{run_id}/evidence` | 查看 EvidenceBundle |
| `GET` | `/api/tasks/{run_id}/evidence/export` | 生成 evidence artifact metadata |
| `GET` | `/api/tasks/{run_id}/evidence/export/content` | 预览 evidence export 内容 |
| `GET` | `/api/tasks/{run_id}/evidence/export/download` | 下载 evidence packet |
| `GET` | `/api/reports/{run_id}` | 读取 Markdown 报告 |
| `GET` | `/api/reports/{run_id}/download` | 下载 Markdown / DOCX / PDF |
| `GET` | `/api/tools` | 列出 Tool Registry 工具 |
| `POST` | `/api/tools/{tool_name}/execute` | 直接调用工具 |
| `GET` | `/mcp/health` | MCP 只读工具服务状态 |
| `GET` | `/mcp/tools` | MCP 工具发现 |
| `POST` | `/mcp/tools/call` | MCP 工具调用 |
| `POST` | `/mcp` | JSON-RPC MCP endpoint |
| `POST` | `/mcp/refresh` | 重新发现远端 MCP 工具 |

## MCP 外部客户端演示

默认离线 demo：

```powershell
.\.venv\Scripts\python.exe scripts\demo_mcp_external_client.py
```

该脚本用 FastAPI `TestClient` 直接调用 `/mcp`，完成：

- `initialize`
- `tools/list`
- `tools/call`
- 携带 `_trace.run_id` 写入 MCP 调用 trace
- 用 `trace_reader` / `report_reader` 读回 trace 和报告

真实 HTTP client smoke：

```powershell
.\.venv\Scripts\python.exe scripts\smoke_mcp_external_http_client.py
```

## Docker

轻量 Docker 默认使用 deterministic/json RAG，不打包本地 embedding 模型：

```powershell
docker compose up --build
```

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

## 验证与测试

离线单元/契约测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\.venv\Scripts\python.exe -m pytest tests
```

常用快速检查：

```powershell
.\.venv\Scripts\python.exe -m compileall app scripts frontend
.\.venv\Scripts\python.exe scripts\smoke_planner.py
.\.venv\Scripts\python.exe scripts\smoke_planner_guardrails.py
.\.venv\Scripts\python.exe scripts\smoke_streamlit_frontend.py
.\.venv\Scripts\python.exe scripts\smoke_mcp_source_pack_bridge.py
```

核心 smoke：

```powershell
.\.venv\Scripts\python.exe scripts\smoke_hitl.py
.\.venv\Scripts\python.exe scripts\smoke_evidence_export.py
.\.venv\Scripts\python.exe scripts\smoke_report_download.py
.\.venv\Scripts\python.exe scripts\smoke_evidence_aggregation.py
.\.venv\Scripts\python.exe scripts\smoke_realtime_trace.py
.\.venv\Scripts\python.exe scripts\smoke_mcp_server.py
.\.venv\Scripts\python.exe scripts\smoke_mcp_client.py
.\.venv\Scripts\python.exe scripts\smoke_mcp_channels.py
.\.venv\Scripts\python.exe scripts\demo_mcp_external_client.py
.\.venv\Scripts\python.exe scripts\smoke_mcp_external_http_client.py
.\.venv\Scripts\python.exe scripts\smoke_parallel_execution.py
.\.venv\Scripts\python.exe scripts\smoke_react_executor.py
.\.venv\Scripts\python.exe scripts\smoke_auth_async.py
.\.venv\Scripts\python.exe -m app.eval.run_eval
```

完整聚合验证：

```powershell
.\.venv\Scripts\python.exe scripts\smoke_final_project.py
```

测试分层说明：

- `tests/` 放离线、快速、无密钥的单元/契约测试，适合作为 CI 基线。
- `scripts/smoke_*.py` 是集成/冒烟脚本，覆盖 FastAPI、Streamlit、MCP、trace、report 等链路。
- `app/eval/` 的 `27/27` 是确定性工程 eval case 数字，不等同于公开 benchmark，也不要求真实 API Key。
- 真实 LLM、真实网页搜索、真实 SentenceTransformers/Chroma 属于可选本地验证，应单独记录运行环境和产物。

## 安全边界

- 不提交 `.env`、API Key、GitHub Token、本地模型、SQLite 数据库、RAG 索引、缓存、导出文件或生成报告。
- 本地文件读取默认限制在 `workspace/docs`，白名单外路径进入 HITL。
- SQL 只允许只读查询。
- GitHub、Tavily、Firecrawl、Exa 均按只读工具处理。
- MCP readonly channel 只自动注册 `read_only=true`、`side_effect_free=true`、`requires_confirmation=false` 的工具。
- interactive/write channel 不进入默认自动执行链路。
- `headers_env` 只记录环境变量名，不把 header 值写入 health、trace、report 或 evidence export。
- 远端工具失败会变成 failed `ToolResult` 和 evidence，不应让任务 API 直接 500。

## 运行产物

以下目录用于本地运行、缓存、导出或测试产物，默认不应提交：

- `workspace/reports/`
- `workspace/exports/`
- `workspace/cache/`
- `workspace/index/`
- `workspace/chroma/`
- `workspace/eval_outputs/`
- `workspace/demo.sqlite`
- `output/`
- `.playwright-cli/`

## 当前状态与路线

当前项目已具备完整 demo 闭环：

- FastAPI task lifecycle
- Streamlit Research Console Lite
- Planned / Parallel / ReAct executor
- ToolTrace persistence
- EvidenceBundle aggregation and export
- Markdown / Word / PDF report download
- MCP readonly server
- Remote MCP channels
- Firecrawl / Exa / Context7 Source Pack bridge

后续可以继续增强：

- Evidence quality / verifier
- Trace replay / run replay
- 更完整的真实网页站点展开策略
- 更多 eval cases 和报告质量评分
- 更完善的 PR/CI 自动化

## License

当前仓库尚未声明开源许可证。公开发布或接受外部贡献前，建议补充 `LICENSE` 文件。
