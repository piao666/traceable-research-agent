# GPT Researcher Reading Notes

Reading date: 2026-06-16

Reference path: `E:\BOSS\_reference_gpt_researcher`

These notes summarize GPT Researcher as a read-only architectural reference.
The Traceable Research Agent implementation remains an independent codebase.

## Files Read

- `README.md`
- `main.py`
- `backend/server/app.py`
- `gpt_researcher/agent.py`
- `gpt_researcher/skills/researcher.py`
- `gpt_researcher/skills/writer.py`
- `gpt_researcher/document/document.py`
- `gpt_researcher/mcp/README.md`
- `gpt_researcher/mcp/client.py`
- `gpt_researcher/mcp/tool_selector.py`
- `gpt_researcher/mcp/research.py`
- `docker-compose.yml`
- `Dockerfile`

## Main Startup Chain

`main.py` is a thin server entrypoint. It creates a `logs` directory, configures
logging, loads `.env`, imports `app` from `backend.server.app`, and starts
Uvicorn on `0.0.0.0:8000` when executed directly.

The package example in the README shows the core programmatic chain:

1. Create `GPTResearcher(query=...)`.
2. Run `await researcher.conduct_research()`.
3. Run `await researcher.write_report()`.

This maps cleanly to our later `create_run -> plan_task -> execute_step ->
tool_call -> trace_write -> generate_report` flow, but our project needs
database-backed run and trace records rather than a single in-memory researcher
object.

## FastAPI Service Reference

`backend/server/app.py` defines the GPT Researcher FastAPI app. Important design
references:

- Uses a lifespan hook to create output directories and mount static files.
- Adds CORS for local frontend and hosted app origins.
- Serves a frontend root route and static assets.
- Exposes report routes such as `/report/{research_id}`, `/report/`, and
  `/api/reports`.
- Exposes local document routes such as `/files/`, `/upload/`, and
  `/files/{filename}`.
- Uses a WebSocket endpoint `/ws` for streaming research progress.
- Calls `run_agent(...)` to execute research and then converts Markdown to Word
  and PDF outputs.
- Uses a lightweight report store, not a relational trace database.

For Traceable Research Agent, the service shape is useful, but the API contract
must be rebuilt around task runs, status, trace querying, report querying, and
tool metadata.

## GPTResearcher Main Flow

`gpt_researcher/agent.py` defines `GPTResearcher`.

`conduct_research`:

- Logs the start event with query, report type, selected agent, and role.
- Handles deep research separately when configured.
- Chooses an agent and role when not provided.
- Delegates context collection to `self.research_conductor.conduct_research()`.
- Optionally pre-generates images before report writing.
- Returns accumulated context.

`write_report`:

- Sets the current step to report writing.
- Delegates final text generation to `self.report_generator.write_report(...)`.
- Passes internal context or external context into the writer.
- Logs report completion with report length and image count.

The useful pattern is separation between orchestration, research collection, and
report generation. The missing piece for this project is structured, persistent
tool-call tracing.

## ResearchConductor Branches

`gpt_researcher/skills/researcher.py` defines `ResearchConductor`.

The main `conduct_research` method chooses the context gathering branch:

- Provided source URLs: scrape specific URLs and optionally complement with web
  search.
- Web source: search and scrape with configured retrievers.
- Local source: load local documents using `DocumentLoader`, optionally load
  them into a vector store, then search over the data.
- Hybrid source: combine local document context and web context.
- Azure source: load Azure container documents, then process through the
  document loader.
- LangChain documents: use provided LangChain documents.
- LangChain vector store: query an existing vector store directly.

The web branch generates sub-queries, processes them concurrently, gathers
context, and can combine cached MCP context with web context. MCP strategy can
be `disabled`, `fast`, or `deep`.

For Traceable Research Agent, this inspires a router/planner that can choose
file, SQL, RAG, and later GitHub/MCP tools. The implementation should be simpler
at first: deterministic JSON plans and explicit Tool Registry calls.

## Report Writer

`gpt_researcher/skills/writer.py` defines `ReportGenerator`.

It builds report parameters from query, role, report type, source, tone,
websocket, config, and headers. It can write:

- Final report body.
- Conclusion.
- Introduction.
- Subtopics.

The key engineering lesson is that the report writer should receive structured
context and task metadata. Traceable Research Agent should generate reports from
observations and evidence trace records, not from untracked raw strings.

## DocumentLoader

`gpt_researcher/document/document.py` defines `DocumentLoader`.

It accepts a directory path or a list of files, walks local files, detects file
extensions, and dispatches to LangChain community loaders. Supported formats
include:

- PDF
- TXT
- DOC/DOCX
- PPTX
- CSV
- XLS/XLSX
- Markdown
- HTML/HTM

It returns dictionaries with `raw_content` and source filename. This is useful
for our future `file_reader` and RAG loader design, but our project must add
stronger path whitelisting under `workspace/docs`, max character limits, and
visible trace rows for success, failure, and safety rejections.

## MCP Module Structure And Safety Lessons

The MCP README describes four components:

- `client.py`: `MCPClientManager` converts configs, creates
  `MultiServerMCPClient`, loads tools, and releases client references.
- `tool_selector.py`: `MCPToolSelector` uses an LLM to select relevant tools and
  falls back to keyword pattern matching.
- `research.py`: `MCPResearchSkill` binds selected tools to an LLM, executes
  tool calls, normalizes results into search-result dictionaries, and continues
  on tool errors.
- `streaming.py`: streaming and logging utilities.

Security lessons:

- Keep tokens in environment variables.
- Connect only to trusted servers.
- Use HTTPS/WSS for remote connections.
- Apply access control around tool permissions.
- Treat tool descriptions and selection as a policy surface.

For this project, MCP should remain a late Phase 4 feature. The safer first step
is a read-only GitHub/API or mock tool with the same Tool Registry interface.

## Useful References To Borrow

- Planner, execution, and publisher/report separation from the README.
- Thin `main.py` server entrypoint.
- FastAPI route organization and local startup pattern.
- Source-mode branching: web, local, hybrid, vector, MCP.
- Document extension handling ideas.
- MCP client lifecycle, tool selection fallback, result normalization, and
  token handling lessons.
- Docker volume pattern for inputs, outputs, and logs.

## Parts That Must Be Rebuilt

- Public API: use `/api/tasks`, `/api/tasks/{run_id}`,
  `/api/tasks/{run_id}/trace`, `/api/reports/{run_id}`, and `/api/tools`.
- Persistence: use SQLite and SQLAlchemy for `agent_runs` and `tool_traces`.
- Tool system: explicit Tool Registry with schemas, risk levels, confirmation
  flags, and trace persistence.
- Safety: enforce file whitelist, read-only SQL, external-call timeouts, and
  visible safety rejection traces.
- Reports: generate Markdown from structured observations and evidence.
- Execution: start with deterministic plans before LLM-generated tool plans.

## Engineering Implications For Traceable Research Agent

The first independent milestone should be a small API shell with stable route
contracts. Day 4-5 can then add SQLite tables and make `POST /api/tasks`
persist a real run. After that, file, SQL, and RAG tools can share one trace
recording path. MCP should not be introduced until the local tool loop,
traceability, and report API are reliable.
