# MCP Read-only Direction

## Current State

The project now has two MCP-related layers:

1. `mcp_github_search` remains a read-only GitHub-style tool adapter. It uses
   deterministic mock data or optional GitHub public API GET requests.
2. `app/mcp/server.py` exposes a bounded MCP-compatible JSON-RPC server
   foundation with `initialize`, `tools/list`, and `tools/call` for read-only
   tools. It also exposes trace/report reader tools for audit workflows.

This is intentionally not a write-capable general MCP hub. It does not expose
mutation tools, repository writes, PR comments, browser actions, or arbitrary
remote tool hosting by default.

## Safety Boundary

- Only read-only, side-effect-free, no-confirmation tools are auto-exposed.
- Issue creation is not implemented.
- Pull request comments and reviews are not implemented.
- Repository mutations and pushes are not implemented.
- Runtime settings cannot enable write methods through the default readonly
  channel.
- Any future write-capable tool must require explicit allowlisting, HITL
  confirmation, and persisted trace evidence before execution.

## Implemented MCP Surface

- `GET /mcp/health`
- `GET /mcp/tools`
- `POST /mcp/tools/call`
- `POST /mcp` JSON-RPC methods:
  - `initialize`
  - `tools/list`
  - `tools/call`

The advertised protocol version is `2024-11-05`. The server maps public MCP tool
names such as `sql_query_readonly` and `github_search` to local tool registry
handlers, then records optional trace evidence for calls with `_trace`.

## Future MCP Work

1. Add broader client compatibility checks across MCP clients and transports.
2. Keep remote discovery normalized into the local Tool Registry.
3. Preserve the read-only allowlist before exposing discovered tools.
4. Deny known write tools and unknown-risk operations by default.
5. Require human confirmation for any separately approved elevated tool.
6. Persist inputs, outputs, status, latency, and errors for every MCP call.

## Why Not a Write-capable MCP Hub Now

The current project goal is a traceable research-agent backend, not a general
tool-hosting platform. A bounded read-only server is enough to demonstrate
external tool discovery, traceable calls, and report/trace retrieval while
keeping credential, mutation, and operational risk small. Production MCP work
should start from governance, HITL, tenant isolation, and transport lifecycle
rather than by simply exposing more tools.
