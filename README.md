# Traceable Research Agent（可追踪调研智能体）

Traceable Research Agent 是一个面向工程实践的可追踪任务型调研智能体后端，支持 Planned / ReAct 双模式执行、LLM 规划、工具调用链、持久化 Trace、真实/混合 RAG、Streamlit 可视化界面、API 鉴权、异步执行、SQL 只读安全校验和 GitHub 只读调研接入。

## 一、项目背景与目标

本项目参考 [GPT Researcher](https://github.com/assafelovic/gpt-researcher) 的架构思想，但聚焦于「可追踪、可控制、可复盘」的 Agent 执行能力，而非开箱即用的网络调研。

核心流程：
```
创建任务 → 审查计划 → 执行工具 → 查看 Trace → 读取报告
```

`POST /api/tasks` 创建待执行任务并持久化计划，用户可在执行前审查计划；执行过程中每一步工具调用（成功、失败、安全拒绝、降级、HITL 等）均写入 SQLite Trace Store，最终生成以证据为基础的 Markdown 调研报告。

---

## 二、核心功能

- **双模式执行**：Planned 固定计划执行（低延迟、高预测性）和 ReAct 动态决策执行（强恢复、可解释）。
- **LLM 规划器**：Qwen / DeepSeek OpenAI-compatible 接口，失败时降级为确定性规划，降级来源在 Trace 中标注。
- **子问题分解（Phase B）**：宽泛任务由 LLM 先分解为 2-4 个独立子问题，提升调研广度。
- **LLM 合成报告（Phase A）**：Reporter 在工具执行完成后调用 LLM 将所有证据合成为带引用的中文研究报告，失败时 fallback 为模板报告。
- **Trace 持久化**：每步记录 input_summary、output_summary、latency_ms、结构化 metadata，全程可审计。
- **真实混合 RAG**：SentenceTransformers + ChromaDB 向量检索，BM25 稀疏检索，RRF 融合，retrieval_mode=dense|bm25|hybrid 可配置。
- **Streamlit 中文演示界面**：三 Tab 布局（任务与规划 / 执行追踪 / 研究报告），一键切换执行模式和数据来源。
- **安全边界**：文件路径白名单、SQLGlot AST 只读校验、GitHub GET-only、HITL 人工确认、ReAct 循环上限。
- **工程化**：API Key 鉴权、Tenant/User 上下文、BackgroundTasks 异步执行、Docker 轻量构建、Alembic 迁移。

---

## 三、架构总览

```
用户 / Streamlit / API 客户端
  → FastAPI API 层
  → Planner（确定性 / LLM / 子问题分解）
  → Planned Executor / ReAct Executor
  → Tool Registry
  → file_reader / sql_query / rag_search / mcp_github_search / report_writer
  → Trace Store（SQLite）
  → Reporter（模板 + LLM 合成）
  → Streamlit 可视化
```

| 层级 | 职责 |
|------|------|
| API 层 | 任务生命周期、计划、执行、异步、HITL、Trace、报告、工具接口 |
| Agent 层 | 确定性/LLM 规划、Planned 顺序执行、有界 ReAct 循环、子问题分解 |
| Tool 层 | 统一注册、allowed_tools 校验、handler 分发、结构化失败、安全元数据 |
| RAG 层 | 确定性/JSON fallback、SentenceTransformers/Chroma、BM25、RRF 融合 |
| 上下文压缩层 | compress_evidence() 按工具类型提取关键信息，控制 LLM prompt 长度 |
| Trace/DB 层 | SQLAlchemy 模型、SQLite 持久化、Alembic 基线、工具 Trace 日志 |
| UI/评测层 | Streamlit 可视化、smoke 脚本、RAG 实验、Planned/ReAct 量化评测 |

技术栈：Python · FastAPI · Pydantic · SQLAlchemy · SQLite · Alembic · SQLGlot · SentenceTransformers · ChromaDB · rank-bm25 · Streamlit · Uvicorn · Docker Compose

---

## 四、执行模式

### Planned 模式（固定计划）

Agent 先生成完整计划，再按步骤顺序执行工具。适合路径可预测的短任务，延迟低，审计简单。

### ReAct 模式（动态决策）

```
Thought → Action → Observation → 循环 → finish
```

每步基于任务描述、已有 Observation 和可用工具动态选择下一步。适合复杂任务、工具失败、空检索结果等场景。循环受 max_steps 和 same_tool_max_calls 双重保护。

| 模式 | 完成率 | 故障恢复 | Trace 质量 | 平均延迟 | 适用场景 |
|------|-------:|--------:|----------:|--------:|---------|
| Planned | 100% | 1/7 | 3.889 | 826ms | 稳定短路径任务 |
| ReAct | 100% | 6/7 | 4.278 | 1299ms | 故障恢复与决策可解释 |

完整 18-case 评测见 [docs/eval_react_vs_planned.md](docs/eval_react_vs_planned.md)。

---

## 五、RAG 能力

### 轻量 Fallback（默认）
- 确定性 embedding 后端 + JSON 向量索引
- 离线友好，适合快速本地演示
- 不需要本地模型或外部 API

### 真实 RAG
- SentenceTransformers 真实语义向量
- 本地 bge-small-zh-v1.5 模型（512 维）
- ChromaDB 持久化向量库

### 混合 RAG
- 稠密语义检索（Dense）
- BM25 稀疏词汇检索
- RRF（Reciprocal Rank Fusion）融合排序
- retrieval_mode=dense|bm25|hybrid

chunk size 实验（9 篇文档，20 条测试 case）：

| 后端 | Chunk Size | Recall@3 | Recall@5 | 平均延迟 |
|------|----------:|--------:|--------:|-------:|
| SentenceTransformers | 256 | 1.0 | 1.0 | 87ms |
| SentenceTransformers | 512 | 1.0 | 1.0 | 52ms |
| SentenceTransformers | 1024 | 1.0 | 1.0 | 35ms |
| 确定性 | 256 | 1.0 | 1.0 | 4ms |
| 确定性 | 512 | 1.0 | 1.0 | 3ms |
| 确定性 | 1024 | 1.0 | 1.0 | 3ms |

当前 demo 语料召回饱和（三组均 1.0），无统计意义上的最优值。推荐 chunk_size=512 作为延迟、chunk 数量和证据粒度的工程折中。详见 [docs/rag_chunk_experiment.md](docs/rag_chunk_experiment.md)。

---

## 六、工具系统与安全边界

可用工具：
- `file_reader`：读取 workspace/docs 白名单文件，拒绝路径穿越
- `sql_query`：SQLGlot AST 校验，仅允许单条 SELECT 或 WITH 语句
- `rag_search`：确定性/真实/BM25/混合四种索引可选
- `mcp_github_search`：仅 GET 只读，支持 mock/public_api 模式，缓存/重试/fallback
- `report_writer`：基于 Trace 和工具观察生成 Markdown 报告

安全控制：
- 每次运行的 allowed_tools 白名单校验
- Tool Registry 注册/启用双重检查
- 文件路径白名单 + 目录穿越拒绝
- SQLGlot AST 验证：仅 SELECT/WITH，拒绝写操作
- GitHub GET-only 策略：禁止 issue 创建、PR 评论、push 等变更操作
- HITL 人工确认（confirmation_required 步骤）
- REACT_MAX_STEPS 和 REACT_SAME_TOOL_MAX_CALLS 循环保护
- LLM JSON 校验失败时结构化 fallback

---

## 七、API 接口

| 方法 | 路径 | 用途 |
|------|------|------|
| GET | `/health` | 服务健康检查（返回执行模式、LLM 配置） |
| POST | `/api/tasks` | 创建待执行任务，持久化计划 |
| GET | `/api/tasks/{run_id}` | 查询运行状态和进度 |
| GET | `/api/tasks/{run_id}/plan` | 查看持久化计划 |
| POST | `/api/tasks/{run_id}/run` | 同步执行 |
| POST | `/api/tasks/{run_id}/run_async` | 后台异步执行（BackgroundTasks） |
| POST | `/api/tasks/{run_id}/confirm` | HITL 确认或拒绝 |
| GET | `/api/tasks/{run_id}/trace` | 查看完整工具/决策 Trace |
| GET | `/api/reports/{run_id}` | 读取生成的 Markdown 报告 |
| GET | `/api/tools` | 列举已注册工具 |
| POST | `/api/tools/{tool_name}/execute` | 通过 API 边界直接执行工具 |

详见 [API 示例](docs/api_examples.md) 和 [Trace 示例](docs/trace_examples.md)。

---

## 八、快速启动

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

返回 `"status": "ok"` 和 `"execution_mode": "planned"` 即表示服务正常。

---

## 九、.env 关键配置

```ini
# 执行模式（planned 为默认，Streamlit UI 可覆盖）
EXECUTION_MODE=planned

# LLM 规划器
LLM_PLANNER_ENABLED=true
LLM_PROVIDER=qwen
LLM_PLANNER_MODE=auto
LLM_MODEL=qwen-plus
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_TIMEOUT_SECONDS=60

# API Key（不提交到 git）
QWEN_API_KEY=sk-...
DEEPSEEK_API_KEY=sk-...

# ReAct 专项
REACT_LLM_PROVIDER=qwen
REACT_LLM_MODEL=qwen-plus

# RAG
RUN_REAL_RAG_CHUNK_EXPERIMENT=false   # true 时运行真实 embedding 实验
```

---

## 十、Streamlit 中文演示界面

```powershell
streamlit run frontend/streamlit_app.py --server.port 8501
```

访问 http://127.0.0.1:8501。界面功能：

**侧边栏（演示控制台）：**
- 🩺 检查后端连接（显示连接状态 + 当前执行模式）
- 📋 场景模板切换（标准调研 / GitHub 只读 / HITL / LLM 规划器）
- ⚙️ 执行模式下拉框（Planned 固定计划 / ReAct 动态决策）
- 🌐 数据来源切换（real 真实 API / mock 离线演示）
- 🔧 高级配置折叠（API Key / Tenant ID / User ID）
- 🔄 刷新全部 / 🗑️ 清空会话

**主区域三 Tab：**

① 任务与规划：
- 任务输入框（自由输入或一键填充示例任务）
- ① 创建任务 / ② 执行任务 按钮
- LLM 规划器激活提示（绿色横幅）
- 执行计划卡片（工具图标 + 中文工具名 + 风险等级色块）
- HITL 确认区域（当任务需要人工确认时出现）

② 执行追踪：
- 总步骤数 / 成功数 / 失败数 / 平均延迟 四项指标
- 工具调用时间线（每步绿色/红色卡片）
- RAG 检索详情展开（retrieval_mode / dense_hit_count / bm25_hit_count）
- ReAct 思考链展开（Thought / Action / Observation）

③ 研究报告：
- 计划步骤数 / 实际执行步骤 / 任务状态 / 执行模式 四项指标
- 完整 Markdown 报告渲染
- ⬇️ 下载 Markdown 报告按钮

---

## 十一、Docker 一键部署

默认 Docker 路径用于本地演示和面试展示，使用 `requirements-docker-light.txt`，
不打包本地 embedding 模型，RAG 默认走 deterministic/json fallback。

一键启动 FastAPI + Streamlit：

```powershell
docker compose up --build
```

访问地址：
- FastAPI health: http://127.0.0.1:8000/health
- Streamlit demo: http://127.0.0.1:8501

可选本地配置：

```powershell
Copy-Item .env.docker.example .env.docker
```

`.env.docker` 可配置 GitHub、Tavily、Qwen、DeepSeek 等本地 key；该文件已被 git 忽略，不能提交真实密钥。默认无 key 也可以启动离线演示。

停止服务：

```powershell
docker compose down
```

可选真实 RAG 模型部署：

```powershell
$env:RAG_MODEL_HOST_PATH="E:/Models/bge-small-zh-v1.5"
docker compose -f docker-compose.yml -f docker-compose.real-rag.yml up --build
```

真实 RAG override 会挂载本地模型到容器内 `/models/bge-small-zh-v1.5`，并启用 SentenceTransformers + ChromaDB。若本地模型不存在，请使用默认轻量部署。

Docker 配置静态检查：

```powershell
python scripts/smoke_docker_config.py
```

---

## 十二、评测与 Smoke 测试

最终验证状态：
- `smoke_final_project.py`：16/16 通过
- 应用评测：27/27 通过，failed=0
- Trace 完整性：1.0
- 可选真实/混合 RAG 验证通过
- 轻量 Docker 构建/启动/健康/停止验证通过
- Phase A/B 集成测试：8/8 通过

核心命令：
```powershell
python -m compileall app tests scripts frontend
python scripts/smoke_final_project.py
python -m app.eval.run_eval
```

## Parallel Multi-tool Execution

Day37 新增 planned 模式下的可选多工具并行执行。默认仍保持稳定串行路径：

```ini
PARALLEL_EXECUTION_ENABLED=false
PARALLEL_MAX_WORKERS=3
PARALLEL_GROUP_STRATEGY=independent_tools
PARALLEL_TIMEOUT_SECONDS=60
```

本地开启方式：

```ini
PARALLEL_EXECUTION_ENABLED=true
PARALLEL_MAX_WORKERS=3
```

当前只并行安全、互不依赖的只读工具：`file_reader`、`rag_search`、
`mcp_github_search`、`tavily_search` 和只读 `sql_query`。`report_writer`
始终作为 evidence 收集后的 barrier 执行；`requires_confirmation=true` 的 HITL
步骤不会并行，也不会被绕过。

每个并行工具调用继续使用现有 `ToolResult` 结构，并在 Trace output metadata
中记录：`parallel`、`parallel_group_id`、`parallel_worker_id`、
`parallel_group_size`、`execution_mode=planned_parallel`、`started_at`、
`finished_at`、`latency_ms`。Reporter 和 Streamlit Trace Viewer 会展示这些
并行组、worker 和耗时信息。单个工具失败会写入 failed trace 并进入报告，不会把
整个 API 调用变成 500。

ReAct 在 Day37 仍保持动态串行 loop；后续可探索 ReAct 内部候选工具并行，但本轮只
增强 planned executor。

Day37 验证命令：

```powershell
python scripts/smoke_parallel_execution.py
```

预期输出：

```json
{
  "parallel_execution": "ok",
  "default_serial_guard": "ok",
  "parallel_group": "ok",
  "trace_metadata": "ok",
  "report_writer_guard": "ok",
  "failure_visible": "ok",
  "hitl_guard": "ok",
  "async_dispatch": "ok"
}
```

---

## 十三、优化路线图（Phase A-E）

### Phase A：LLM 合成报告 ✅ 已完成

新增 `app/agent/context_compressor.py`：按工具类型压缩证据，防止 prompt 溢出。

reporter.py 新增 `_llm_synthesize_answer()`：调用 LLM 将工具证据合成为带引用的中文回答，失败时自动 fallback 为模板报告。

### Phase B：Sub-query 任务分解 ✅ 已完成

新增 `app/agent/query_decomposer.py`：LLM 将宽泛任务分解为 2-4 个独立子问题，结果存入 `plan["sub_queries"]`。LLM 不可用时静默跳过，不影响正常执行。

### Phase C：并行工具执行 🔲 规划中

目标：将串行 for 循环改为 `asyncio.gather` 并发执行，速度提升 3-5x。

检索类工具（rag_search / tavily_search / mcp_github_search）并行执行；sql_query 串行；report_writer 等其他工具完成后执行。

### Phase D：WebSocket 实时进度推送 🔲 规划中

目标：新增 `/ws/{run_id}` WebSocket 接口，每 500ms 推送最新 Trace 和状态，替换 Streamlit 轮询模式。面试演示时可以实时看 Agent 思考过程。

### Phase E：三层 LLM 策略 🔲 规划中

目标：按用途分配模型，降本提质。
- Planner → smart_llm（qwen-plus）
- ReAct 每步 → fast_llm（qwen-turbo）
- Reporter 合成 → strategic_llm（deepseek-chat）

---

## 十四、目录结构

```
traceable-research-agent/
├── app/
│   ├── agent/
│   │   ├── context_compressor.py  # [新] Phase A 证据压缩
│   │   ├── query_decomposer.py    # [新] Phase B 任务分解
│   │   ├── dispatcher.py          # 执行模式路由
│   │   ├── executor.py            # Planned 执行器
│   │   ├── planner.py             # LLM/确定性规划器
│   │   ├── react_executor.py      # ReAct 执行器
│   │   ├── react_prompt.py        # ReAct 系统提示词
│   │   ├── react_schema.py        # ReAct 决策 Schema
│   │   └── reporter.py            # 报告生成器（含 LLM 合成）
│   ├── api/
│   │   └── tasks.py               # 任务生命周期接口
│   ├── eval/                      # 评测脚本
│   ├── llm/
│   │   ├── base.py                # LLMClient 抽象
│   │   ├── planner_client.py      # LLM 规划器调用
│   │   └── providers.py           # OpenAI-compatible 客户端
│   ├── rag/                       # RAG 检索层
│   ├── tools/                     # 工具实现
│   └── trace/                     # SQLite Trace Store
├── docs/                          # 设计文档
├── frontend/
│   └── streamlit_app.py           # 中文演示界面
├── migrations/                    # Alembic 迁移
├── scripts/                       # smoke 测试 / 初始化脚本
├── workspace/
│   └── docs/                      # 9 篇 demo 语料文件
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── requirements-docker-light.txt
```

---

## 十五、当前限制

- Streamlit 是演示界面，非生产前端。
- `BackgroundTasks` 是本地异步，非分布式任务队列。
- Tenant/User 上下文仅限请求级，非生产多租户隔离。
- MCP 支持是只读适配器，非完整 MCP 服务器。
- 默认 ReAct 评测使用 fake/mock LLM 决策保证可复现；真实 LLM 评测需配置 API Key。
- Docker image 使用轻量依赖，不含本地 embedding 模型。
- RAG 实验当前语料召回饱和，无统计意义上的最优 chunk size。
- Phase C-E（并行执行、WebSocket、三层 LLM）尚在规划中。

---

## 十六、文档索引

- [系统架构](docs/architecture.md)
- [API 示例](docs/api_examples.md)
- [Trace 示例](docs/trace_examples.md)
- [坏例分析](docs/bad_cases.md)
- [面试备注](docs/interview_notes.md)
- [MCP 只读方向](docs/mcp_readonly_direction.md)
- [RAG Chunk 实验](docs/rag_chunk_experiment.md)
- [ReAct vs Planned 评测](docs/eval_react_vs_planned.md)
- [项目功能矩阵](docs/project_feature_matrix.md)
- [演示脚本](docs/demo_script.md)
- [项目最终总结](docs/final_project_summary.md)
- [面试陈述稿](docs/interview_pitch.md)
- [Day35 最终工程检查点](docs/checkpoints/day35_final_engineering_checkpoint.md)

---

## Full MCP Server Foundation

Day38 adds a lightweight, read-only MCP-compatible server foundation.

Endpoints:

```text
GET  /mcp/health
GET  /mcp/tools
POST /mcp/tools/call
POST /mcp
```

`POST /mcp` supports a small JSON-RPC subset:

- `initialize`
- `tools/list`
- `tools/call`

Initial exposed MCP tools:

- `file_reader`
- `rag_search`
- `sql_query_readonly`
- `github_search`
- `tavily_search`
- `trace_reader`
- `report_reader`

MCP metadata includes `read_only`, `side_effect_free`, `requires_confirmation`,
`risk_level`, `input_schema`, and `output_schema`. Write-capable tools and barrier
tools such as `report_writer` are not exposed by default.

Read-only policy notes:

- GitHub remains GET-only through the existing public API adapter.
- Tavily search is allowed as semantic read-only search even though its HTTP API uses POST.
- SQL remains protected by the existing SQLGlot read-only validation.
- MCP calls can optionally write trace rows when a `run_id` is supplied in trace options.

Day38 smoke:

```powershell
python scripts/smoke_mcp_server.py
```

Expected output:

```json
{
  "mcp_server": "ok",
  "tool_discovery": "ok",
  "json_rpc": "ok",
  "tool_call_trace": "ok",
  "readers": "ok",
  "write_tools_hidden": "ok"
}
```

---

> 永远不要提交 `.env`、API Key、GitHub Token、本地模型文件、SQLite 数据库、生成的报告、缓存文件、索引文件、Chroma 数据或评测输出到 git。
