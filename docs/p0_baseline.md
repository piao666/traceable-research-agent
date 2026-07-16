# P0 Reproducible Baseline

P0 makes report generation and offline verification explicit. It does not add
new research capabilities.

## Runtime Modes

| Concern | Offline/default | Live provider |
| --- | --- | --- |
| Report generation | `REPORT_GENERATION_MODE=deterministic` | `REPORT_GENERATION_MODE=llm` |
| Planner | `LLM_PLANNER_ENABLED=false` | Explicit opt-in |
| External tools | mock or disabled | Explicit Provider credentials |
| MCP registry | disabled or fake bridge | Explicit remote server configuration |

`REPORT_GENERATION_MODE=deterministic` never creates a report synthesis call,
even if Qwen or DeepSeek credentials exist in the process environment. `llm`
mode fails configuration validation unless a supported Provider and credential
are configured.

## Offline Verification

```powershell
.venv\Scripts\python.exe -m compileall -q app scripts frontend tests
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\python.exe scripts\smoke_e2e.py
.venv\Scripts\python.exe scripts\smoke_evidence_aggregation.py
.venv\Scripts\python.exe scripts\smoke_evidence_export.py
.venv\Scripts\python.exe scripts\smoke_mcp_source_pack_bridge.py
.venv\Scripts\python.exe scripts\smoke_react_vs_planned_eval.py
.venv\Scripts\python.exe scripts\smoke_realtime_trace.py
.venv\Scripts\python.exe scripts\smoke_report_download.py
.venv\Scripts\python.exe scripts\smoke_auth_async.py
```

CI fixes `OFFLINE_MODE=true`, deterministic report generation, mock external
tools, and disables remote MCP registration.

## Failure Contract

Provider-specific `error_type` values remain available for compatibility.
Tool Registry also writes a stable `error_category` such as `timeout`,
`rate_limited`, `auth_error`, `provider_error`, `invalid_result`, or
`internal_error`. Policy, invalid request, unavailable, and not-found failures
have separate stable categories.

Tool Registry sanitizes ToolResult values and Trace Logger sanitizes them again
before SQLite persistence. Evidence export uses the same redaction policy.

## Verified Baseline (2026-07-16)

- Local compilation passed.
- Local unit contracts passed: 14 tests and 7 subtests.
- Forced-offline aggregate suite passed: 21/21 checks.
- Ten additional evidence, MCP, parallel, SSE, Tavily, RAG-switch, and Docker
  configuration smoke checks passed.
- A normal local E2E run completed in 2.4 seconds while real Provider keys
  remained present, proving deterministic reports do not implicitly call them.
- Docker Server 29.5.2 built the current API and Streamlit images.
- API and Streamlit health endpoints returned success.
- Container API E2E completed with three tool traces and a generated report.
- After restarting the API container, task status, report access, and Markdown
  download remained available from the mounted workspace.
- Container tests passed: 14 tests and 7 subtests.

Credential rotation for any previously used real Provider keys remains an
operator action in the corresponding Provider consoles. No credential value is
stored in this document or committed configuration.
