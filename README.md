# Traceable Research Agent

Traceable Research Agent 是一个面向工程审计和演示的可追踪调研 Agent。它把任务规划、工具调用、实时追踪、证据聚合、Markdown 报告和证据导出放在同一条闭环里，让一次 Agent 运行不仅能给出答案，也能说明答案来自哪里、哪些工具成功或失败、哪些证据是 mock/fallback、哪些结论需要保留限制。

项目默认可以离线运行；配置 API Key 后可启用真实 LLM、GitHub Public API、Tavily Search 和真实 RAG 后端。Streamlit 是当前主交互界面，FastAPI 提供完整后端 API，MCP 端点用于外部只读工具发现和调用演示。

## 核心能力

- 任务规划：支持确定性 Planner 和 OpenAI-compatible LLM Planner，LLM 不可用时稳定降级。
- Planner 参数稳定化：在计划落库前归一化文件路径、SQL 查询、GitHub 查询、RAG/Tavily 参数，避免自由问题产生超出本地工具边界的参数。
- 双执行模式：Planned Executor 适合稳定计划；ReAct Executor 适合逐步观察、失败恢复和限制性完成。
- 多工具系统：本地文件读取、只读 SQL、RAG 检索、GitHub 只读搜索、Tavily 搜索、报告生成，以及可选远端 MCP 工具。
- 并行执行：Planned 模式可选择并行执行安全、互不依赖的只读工具，并保留完整 trace metadata。
- 实时追踪：REST trace API 和 SSE 事件流记录运行状态、工具输入输出摘要、延迟、错误和元数据。
- 证据聚合：从 ToolTrace 中提取 EvidenceBundle，按来源、状态、mock/fallback、失败和 unsupported claims 归类。
- 证据导出：支持 JSON、JSONL、Markdown 三种 evidence packet，可预览、下载，并过滤明显密钥字段。
- 报告生成：基于计划、观察、Trace 和 EvidenceBundle 生成 Markdown 调研报告。
- 安全边界：文件白名单、SQLGlot 只读校验、GitHub GET-only 策略、API Key 鉴权、Tenant/User 请求上下文、HITL 确认、ReAct 循环上限。

## 架构总览

```text
Client / Streamlit / MCP Client
        |
        v
FastAPI API
        |
        v
Planner
  - deterministic planner
  - LLM planner
  - plan argument guardrails
        |
        v
Executor
  - planned executor
  - optional parallel executor
  - ReAct executor
        |
        v
Tool Registry
  - file_reader
  - sql_query
  - rag_search
  - mcp_github_search
  - tavily_search
  - remote MCP tools
        |
        v
Trace Store / Evidence / Reporter / Exporter
        |
        v
Markdown report + JSON/JSONL/Markdown evidence packets
```

主要目录：

```text
app/api/        FastAPI routers: tasks, reports, tools, events, MCP
app/agent/      Planner, executors, reporter, evidence, export, guardrails
app/tools/      Tool Registry and local tool implementations
app/rag/        Dense/BM25/hybrid retrieval backends
app/mcp/        Read-only MCP server and optional remote MCP client
app/trace/      SQLAlchemy models, trace persistence, SSE formatting
frontend/       Streamlit demo UI
scripts/        init/build/smoke/eval scripts
workspace/docs/ Demo corpus used by file_reader and RAG
```

## 快速启动

```powershell
cd E:\BOSS\traceable-research-agent
python -m pip install -r requirements.txt
python scripts/init_demo_db.py
python scripts/build_rag_index.py
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health | ConvertTo-Json -Depth 10
```

启动 Streamlit：

```powershell
streamlit run frontend/streamlit_app.py --server.port 8501
```

访问：

- FastAPI: `http://127.0.0.1:8000`
- Streamlit: `http://127.0.0.1:8501`
- OpenAPI docs: `http://127.0.0.1:8000/docs`

## 配置

复制 `.env.example` 为 `.env` 后按需填写。默认配置不需要真实 API Key，也能完成离线 demo 和 smoke 测试。

常用配置：

```ini
EXECUTION_MODE=planned
PARALLEL_EXECUTION_ENABLED=false

LLM_PLANNER_ENABLED=false
LLM_PROVIDER=qwen
LLM_PLANNER_MODE=auto
LLM_MODEL=qwen-plus
QWEN_API_KEY=
DEEPSEEK_API_KEY=

EXTERNAL_TOOLS_DEFAULT_MODE=real
GITHUB_TOOL_DEFAULT_MODE=public_api
GITHUB_TOKEN=
GITHUB_PUBLIC_API_FALLBACK_TO_MOCK=false

TAVILY_API_KEY=
TAVILY_FALLBACK_TO_MOCK=false

FILE_READER_ALLOWED_ROOTS=workspace/docs
FILE_READER_HITL_OUTSIDE_ALLOWED_ROOTS=true

RAG_EMBEDDING_BACKEND=deterministic
RAG_VECTOR_BACKEND=json
RAG_REAL_BACKEND_ENABLED=false
```

真实 RAG 可切换到 SentenceTransformers + Chroma：

```ini
RAG_EMBEDDING_BACKEND=sentence_transformers
RAG_VECTOR_BACKEND=chroma
RAG_REAL_BACKEND_ENABLED=true
RAG_MODEL_PATH=E:\Models\bge-small-zh-v1.5
```

不要提交 `.env`、API Key、GitHub Token、本地模型、SQLite 数据库、RAG 索引、缓存、导出文件或生成报告。

## API 使用

创建任务：

```powershell
$body = @{
  task = "Read local docs, query database metrics, retrieve evidence, and generate a markdown report"
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

常用接口：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/health` | 服务状态和安全配置摘要 |
| `POST` | `/api/tasks` | 创建任务并持久化计划 |
| `GET` | `/api/tasks/{run_id}` | 查询运行状态 |
| `GET` | `/api/tasks/{run_id}/plan` | 查看计划 |
| `POST` | `/api/tasks/{run_id}/run` | 同步执行 |
| `POST` | `/api/tasks/{run_id}/run_async` | 后台执行 |
| `POST` | `/api/tasks/{run_id}/confirm` | HITL 确认或拒绝 |
| `GET` | `/api/tasks/{run_id}/trace` | 查看 ToolTrace |
| `GET` | `/api/tasks/{run_id}/events` | SSE 实时事件流 |
| `GET` | `/api/reports/{run_id}` | 读取 Markdown 报告 |
| `GET` | `/api/reports/{run_id}/download` | 下载 Markdown/Word/PDF 报告 |
| `GET` | `/api/tasks/{run_id}/evidence` | 读取 EvidenceBundle |
| `GET` | `/api/tasks/{run_id}/evidence/export` | 生成导出 artifact 并返回 metadata |
| `GET` | `/api/tasks/{run_id}/evidence/export/content` | 生成并返回可预览内容 |
| `GET` | `/api/tasks/{run_id}/evidence/export/download` | 下载导出文件 |
| `GET` | `/api/tools` | 列出工具 |
| `POST` | `/api/tools/{tool_name}/execute` | 直接调用工具 |

## Streamlit 交互

Streamlit 页面提供三类主流程：

- 任务与规划：输入任务、选择模板、选择 Planned/ReAct、切换 real/mock、创建任务、查看计划、执行任务、处理 HITL。
- 执行追踪：查看状态指标、工具时间线、错误、延迟、RAG/GitHub/MCP 元数据和 ReAct observation。
- 研究报告：渲染 Markdown 报告，下载 Markdown/Word/PDF 报告，查看 EvidenceBundle 摘要，导出并预览 JSON/JSONL/Markdown evidence packet，直接下载导出内容。

## 工具边界

- `file_reader`：默认允许读取 `workspace/docs` 下支持的文本文件；可通过 `FILE_READER_ALLOWED_ROOTS` 配置更多白名单根目录。Planner 遇到白名单外路径时不会静默回退到 demo 文档，而是将对应 step 标记为 HITL，批准后也只允许读取该次确认的具体文件路径。
- `sql_query`：只允许单条 `SELECT` 或 `WITH`；Planner 会把未知列/表修正为 demo schema 下的安全查询。
- `rag_search`：支持 `dense`、`bm25`、`hybrid`；默认离线 JSON index，可切换真实向量后端。
- `mcp_github_search`：只读 GitHub 搜索；`repo` 必须是 `owner/name` 或 `null`；Planner 会压缩过长 query 并对齐 real/mock 模式。
- `tavily_search`：只读外部搜索；需要 Tavily API Key，否则按配置返回结构化失败或 fallback。
- `report_writer`：不作为外部可写工具暴露，由 Reporter 基于 trace 和 evidence 生成报告。

## MCP

项目提供只读 MCP-compatible server：

```text
GET  /mcp/health
GET  /mcp/tools
POST /mcp/tools/call
POST /mcp
```

JSON-RPC 支持：

- `initialize`
- `tools/list`
- `tools/call`

MCP 默认只暴露 read-only、side-effect-free、无需确认的工具；`report_writer` 等 barrier/write-like 工具不会对外暴露。远端 MCP 工具注册默认关闭，可通过环境变量启用：

```ini
MCP_REMOTE_REGISTRY_ENABLED=true
MCP_REMOTE_SERVERS=[{"name":"demo","base_url":"http://127.0.0.1:8001/mcp","timeout_seconds":5}]
```

新版远端 MCP 推荐使用通道配置：

```ini
MCP_CHANNEL_READONLY_SERVERS=[
  {"name":"firecrawl","base_url":"http://127.0.0.1:9001/mcp","transport":"http_json_rpc","timeout_seconds":10,"allowed_tools":["search","scrape","map","extract"],"blocked_tools":[],"headers_env":{"Authorization":"FIRECRAWL_MCP_AUTH_HEADER"},"channel":"readonly"},
  {"name":"exa","base_url":"http://127.0.0.1:9002/mcp","transport":"http_json_rpc","timeout_seconds":10,"allowed_tools":["web_search_exa","web_fetch_exa","web_search_advanced_exa"],"blocked_tools":[],"headers_env":{"Authorization":"EXA_MCP_AUTH_HEADER"},"channel":"readonly"},
  {"name":"context7","base_url":"http://127.0.0.1:9003/mcp","transport":"http_json_rpc","timeout_seconds":10,"allowed_tools":["resolve-library-id","query-docs"],"blocked_tools":[],"headers_env":{},"channel":"readonly"}
]
MCP_CHANNEL_INTERACTIVE_SERVERS=[{"name":"browser","base_url":"http://127.0.0.1:9004/mcp","transport":"http_json_rpc","allowed_tools":["navigate","screenshot"],"channel":"interactive"}]
MCP_CHANNEL_WRITE_SERVERS=[]
```

通道策略：

- `readonly`：只有 `tools/list` 声明 `read_only=true`、`side_effect_free=true`、`requires_confirmation=false` 的工具会自动注册和自动执行。
- `interactive`：用于 browser/playwright 等导航、点击、截图类工具；工具会注册为需要 HITL，不进入默认只读 MCP 暴露和并行自动执行。
- `write`：默认禁用，即使配置也不会自动注册为可执行工具。

每个 server 支持 `name`、`base_url`、`transport`、`timeout_seconds`、`allowed_tools`、`blocked_tools`、`headers_env` 和 `channel`。`headers_env` 只记录环境变量名，不会把 header 值写入 health、trace、report 或 evidence export。远端工具会注册为 `<server>.<tool>`，并在 trace metadata 中标记 `tool_source=mcp_remote`、`remote_server`、`remote_channel` 和 `remote_tool_name`。远端失败会变成 failed `ToolResult`，不会把任务 API 变成 500。

Firecrawl 如果以 HTTP JSON-RPC MCP 形态提供只读网页抓取/搜索工具，可以放入 `readonly` 通道。browser/filesystem/database MCP 应只接入经过 allowlist 限定的只读子集；browser/playwright 类交互工具应放入 `interactive` 通道。当前实现不是无限制 MCP hub，而是可配置、可审计、默认安全的可信子集。

### Deep Research Source Pack

板块三新增一个明确的 MCP 产品场景：深度网页调研。它面向“已经能搜到 URL，但需要读正文、抽取证据、展开站点结构并生成可审计报告”的任务。

- Streamlit 模板 `深度网页调研（Tavily + Firecrawl/Exa MCP）`：用 Tavily/Exa 做发现，用 Firecrawl `search/scrape/map/extract` 读取页面正文和结构，再进入 EvidenceBundle 与报告。
- Streamlit 模板 `技术文档调研（GitHub + Context7/Exa MCP）`：用 GitHub 查代码和 issue，用 Context7 `resolve-library-id/query-docs` 查当前库文档，用 Exa/Firecrawl 补充网页来源。
- EvidenceBundle 会把 remote MCP 搜索/map 结果标记为 `mcp_remote_discovery`，把 scrape/extract/fetch/query-docs 正文标记为 `mcp_remote_support`，失败标记为 `mcp_remote_failure`。
- 默认离线 demo 不要求配置任何远端 MCP；未配置 Firecrawl/Exa/Context7 时，Planner 会保留内置 Tavily/GitHub/RAG 路径并在 notes 中记录降级。

### MCP External Client Demo

Day46 提供一个默认离线可跑的外部 MCP client demo：

```powershell
python scripts/demo_mcp_external_client.py
```

该脚本使用 FastAPI `TestClient` 直接调用本项目 `/mcp`，不需要先启动 uvicorn。它会先创建并执行一个最小 demo run，然后以外部 Agent 的视角完成：

- JSON-RPC `initialize`
- JSON-RPC `tools/list`
- JSON-RPC `tools/call`
- 携带 `_trace.run_id` 的 MCP 调用 trace 写入
- `trace_reader` / `report_reader` 读回 trace 和 Markdown report

脚本输出结构化 JSON summary，包括 `run_id`、`discovered_tools`、`boundary_checks`、`trace_count`、`mcp_trace_written`、`report_exists` 和 `overall_status`。MCP external client demo 只验证只读审计边界：`report_writer` 和写类/高风险工具不会暴露给外部 MCP client。

如需验证真实进程外 HTTP client，可运行：

```powershell
python scripts/smoke_mcp_external_http_client.py
```

该 smoke 会启动一个临时 uvicorn 子进程，并用 `requests` 通过 localhost TCP 调用 `/mcp`，用于确认外部 Agent 按 HTTP JSON-RPC 接入时也能完成工具发现、只读调用、trace 写入和 report readback。

## Docker

轻量 Docker 默认使用 deterministic/json RAG，不打包本地 embedding 模型：

```powershell
docker compose up --build
```

访问：

- FastAPI health: `http://127.0.0.1:8000/health`
- Streamlit demo: `http://127.0.0.1:8501`

停止：

```powershell
docker compose down
```

真实 RAG Docker override：

```powershell
$env:RAG_MODEL_HOST_PATH="E:/Models/bge-small-zh-v1.5"
docker compose -f docker-compose.yml -f docker-compose.real-rag.yml up --build
```

## 验证

常用验证命令：

```powershell
python -m compileall app scripts frontend
python scripts/smoke_hitl.py
python scripts/smoke_planner_guardrails.py
python scripts/smoke_evidence_export.py
python scripts/smoke_report_download.py
python scripts/smoke_evidence_aggregation.py
python scripts/smoke_realtime_trace.py
python scripts/smoke_mcp_server.py
python scripts/smoke_mcp_client.py
python scripts/smoke_mcp_channels.py
python scripts/demo_mcp_external_client.py
python scripts/smoke_mcp_external_http_client.py
python scripts/smoke_parallel_execution.py
python scripts/smoke_react_executor.py
python scripts/smoke_auth_async.py
python -m app.eval.run_eval
```

完整聚合验证：

```powershell
python scripts/smoke_final_project.py
```

## 运行产物

以下目录用于本地运行产物，默认不应提交：

- `workspace/reports/`
- `workspace/exports/`
- `workspace/cache/`
- `workspace/index/`
- `workspace/chroma/`
- `workspace/eval_outputs/`
- `workspace/demo.sqlite`

生成的报告和 evidence packet 用于本地审计、演示和回放，不是源码资产。
