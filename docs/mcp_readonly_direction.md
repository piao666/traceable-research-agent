# MCP Read-only Direction

## Current State

`mcp_github_search` is a read-only MCP/GitHub-style adapter for traceable
research evidence. It supports deterministic mock results and optional GitHub
public API GET requests. It is not a full MCP server.

## Safety Boundary

- Only HTTP GET operations are allowed.
- Issue creation is not implemented.
- Pull request comments and reviews are not implemented.
- Repository mutations and pushes are not implemented.
- Runtime settings cannot enable write methods; write-oriented configuration is
  reported as ignored.
- Any future write-capable tool must require explicit allowlisting, HITL
  confirmation, and persisted trace evidence before execution.

## Future MCP Design

1. Add an MCP client abstraction with bounded lifecycle and timeouts.
2. Discover tools and normalize their schemas into the local Tool Registry.
3. Apply a read-only allowlist before exposing discovered tools.
4. Deny known write tools and unknown-risk operations by default.
5. Require human confirmation for any separately approved elevated tool.
6. Persist inputs, outputs, status, latency, and errors for every MCP call.

## Why Not a Full MCP Server Now

The current project goal is a traceable research-agent backend, not a general
tool-hosting platform. A lightweight read-only adapter is sufficient for the
offline demo and substantially reduces credential, mutation, and operational
risk. A full MCP server remains future roadmap work after the read-only client
contract and policy boundary are stable.
