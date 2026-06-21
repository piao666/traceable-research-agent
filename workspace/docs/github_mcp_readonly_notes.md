# GitHub and MCP Read-only Notes

## Offline and Public Modes

`mcp_github_search` is a read-only GitHub/MCP-style evidence adapter. Mock mode
is the default and requires no network or token. It returns deterministic demo
evidence so local Agent runs, smoke tests, and interviews remain reproducible.

Public API mode uses the GitHub search API through GET requests only. An
optional token can increase rate limits, but token values are never stored in
cache data, trace metadata, or reports. Query, repository, limit, and mode form
the visible tool boundary.

## Cache, Retry, and Fallback

Successful public results can be stored in a local TTL cache. A cache hit is
labeled with `data_source=cache`. Network calls have a timeout and bounded
retry with backoff. HTTP 403/429 can be classified as rate limited; network
errors and invalid JSON have separate stable error types.

When public API access fails and fallback is enabled, the adapter returns mock
evidence with `data_source=fallback`, `fallback_used=true`, the original error
type, and a non-sensitive fallback reason. The report can continue while still
showing that the evidence did not come from the live API.

## MCP Safety Direction

The current MCP integration is a compatible read-only adapter, not a full MCP
server. Its HTTP policy is GET-only. POST, PUT, PATCH, and DELETE are denied,
even if a write-related environment setting is accidentally enabled. The
project exposes no issue creation, pull-request comment, repository mutation,
or push operation.

A future full MCP client/server design would require tool discovery, explicit
read allowlists, write denylists, human elevation for write tools, and trace
persistence for every call. Those production features are intentionally
outside the frozen demo scope.
