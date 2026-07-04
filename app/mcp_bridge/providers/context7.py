"""Context7 Source Pack provider placeholder.

Context7 is commonly consumed as an MCP service. This bridge keeps a stable
HTTP JSON-RPC shape for demos and future REST adapter work without guessing a
non-confirmed public REST contract.
"""

from __future__ import annotations

import os
import re
from typing import Any

from app.mcp_bridge.providers.base import SourcePackProvider
from app.mcp_bridge.schemas import BridgeTool, BridgeToolResult, json_schema


class Context7Provider(SourcePackProvider):
    name = "context7"

    @property
    def api_key(self) -> str:
        return os.getenv("CONTEXT7_API_KEY", "").strip()

    @property
    def base_url(self) -> str:
        return os.getenv("CONTEXT7_BASE_URL", "").strip().rstrip("/")

    def list_tools(self) -> list[BridgeTool]:
        return [
            BridgeTool(
                name="context7.resolve-library-id",
                description="Resolve a library name into a Context7-style library id.",
                input_schema=json_schema(
                    {
                        "libraryName": {"type": "string"},
                        "query": {"type": "string"},
                    },
                ),
                tags=["context7", "docs", "discovery"],
            ),
            BridgeTool(
                name="context7.query-docs",
                description="Query current technical documentation snippets.",
                input_schema=json_schema(
                    {
                        "libraryId": {"type": "string"},
                        "library_id": {"type": "string"},
                        "query": {"type": "string"},
                    },
                    ["query"],
                ),
                tags=["context7", "docs", "support"],
            ),
        ]

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> BridgeToolResult:
        local_name = tool_name.split(".", 1)[-1]
        if self.fake_mode:
            return self._fake(local_name, arguments)
        if self.base_url:
            return self._http_placeholder(local_name, arguments)
        return self._failure(
            local_name,
            "Context7 real adapter is not configured. Use MCP_BRIDGE_FAKE_MODE=true or set CONTEXT7_BASE_URL after confirming the HTTP API contract.",
            error_type="adapter_not_configured",
        )

    def _http_placeholder(self, local_name: str, arguments: dict[str, Any]) -> BridgeToolResult:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        endpoint = "resolve-library-id" if local_name == "resolve-library-id" else "query-docs"
        data, failure = self._post_json(
            f"{self.base_url}/{endpoint}",
            headers=headers,
            payload=arguments,
            tool_name=local_name,
        )
        if failure:
            return failure
        role = "discovery" if local_name == "resolve-library-id" else "support"
        return BridgeToolResult(
            success=True,
            output=data,
            output_summary=f"Context7 {local_name} returned HTTP adapter data.",
            metadata={**self._metadata(local_name, data_source="real_api"), "evidence_role": role},
        )

    def _fake(self, local_name: str, arguments: dict[str, Any]) -> BridgeToolResult:
        if local_name == "resolve-library-id":
            name = str(arguments.get("libraryName") or arguments.get("query") or "FastAPI")
            library_id = _library_id(name)
            return BridgeToolResult(
                success=True,
                output={
                    "results": [
                        {
                            "title": f"{name} documentation",
                            "libraryId": library_id,
                            "url": f"https://context7.com/{library_id.strip('/')}",
                            "content": f"Resolved {name} to fake Context7 library id {library_id}.",
                        }
                    ]
                },
                output_summary=f"Context7 fake resolver returned {library_id}.",
                metadata={**self._metadata(local_name, data_source="fake"), "evidence_role": "discovery"},
            )
        if local_name == "query-docs":
            query = str(arguments.get("query") or "technical docs")
            library_id = str(arguments.get("libraryId") or arguments.get("library_id") or _library_id(query))
            return BridgeToolResult(
                success=True,
                output={
                    "documents": [
                        {
                            "title": f"{library_id} fake current docs",
                            "libraryId": library_id,
                            "url": f"https://context7.com/{library_id.strip('/')}",
                            "content": f"Fake Context7 documentation snippet for query: {query}. Use this as offline demo evidence only.",
                        }
                    ]
                },
                output_summary="Context7 fake query-docs returned 1 documentation snippet.",
                metadata={**self._metadata(local_name, data_source="fake"), "evidence_role": "support"},
            )
        return self._failure(local_name, f"Unknown Context7 fake tool: {local_name}", error_type="unknown_tool")


def _library_id(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_.-]+", "-", name.strip().lower()).strip("-")
    return f"/fake/{slug or 'library'}"

