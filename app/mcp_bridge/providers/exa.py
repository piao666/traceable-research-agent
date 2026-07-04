"""Exa Source Pack provider."""

from __future__ import annotations

import os
from typing import Any

from app.mcp_bridge.providers.base import SourcePackProvider, trim_text
from app.mcp_bridge.schemas import BridgeTool, BridgeToolResult, json_schema


class ExaProvider(SourcePackProvider):
    name = "exa"

    @property
    def api_key(self) -> str:
        return os.getenv("EXA_API_KEY", "").strip()

    @property
    def base_url(self) -> str:
        return os.getenv("EXA_BASE_URL", "https://api.exa.ai").strip().rstrip("/")

    def list_tools(self) -> list[BridgeTool]:
        return [
            BridgeTool(
                name="exa.web_search_exa",
                description="Semantic web search with Exa.",
                input_schema=json_schema(
                    {
                        "query": {"type": "string"},
                        "numResults": {"type": "integer", "default": self.max_results},
                    },
                    ["query"],
                ),
                tags=["exa", "search", "discovery"],
            ),
            BridgeTool(
                name="exa.web_search_advanced_exa",
                description="Advanced Exa semantic search with optional domain/date filters.",
                input_schema=json_schema(
                    {
                        "query": {"type": "string"},
                        "numResults": {"type": "integer", "default": self.max_results},
                        "includeDomains": {"type": "array", "items": {"type": "string"}},
                        "excludeDomains": {"type": "array", "items": {"type": "string"}},
                        "startPublishedDate": {"type": "string"},
                        "endPublishedDate": {"type": "string"},
                    },
                    ["query"],
                ),
                tags=["exa", "search", "advanced", "discovery"],
            ),
            BridgeTool(
                name="exa.web_fetch_exa",
                description="Fetch full page contents for one URL with Exa Contents.",
                input_schema=json_schema(
                    {
                        "url": {"type": "string"},
                        "urls": {"type": "array", "items": {"type": "string"}},
                    },
                ),
                tags=["exa", "fetch", "support"],
            ),
        ]

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> BridgeToolResult:
        local_name = tool_name.split(".", 1)[-1]
        if self.fake_mode:
            return self._fake(local_name, arguments)
        if not self.api_key:
            return self._failure(local_name, "EXA_API_KEY is not configured.", error_type="missing_api_key")
        if local_name in {"web_search_exa", "web_search_advanced_exa"}:
            return self._search(local_name, arguments)
        if local_name == "web_fetch_exa":
            return self._fetch(arguments)
        return self._failure(local_name, f"Unknown Exa tool: {tool_name}", error_type="unknown_tool")

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
        }

    def _search(self, local_name: str, arguments: dict[str, Any]) -> BridgeToolResult:
        query = str(arguments.get("query") or "").strip()
        if not query:
            return self._failure(local_name, "Missing required argument: query", error_type="invalid_arguments")
        num_results = min(int(arguments.get("numResults") or arguments.get("limit") or self.max_results), self.max_results)
        payload: dict[str, Any] = {
            "query": query,
            "numResults": num_results,
            "contents": {"highlights": True, "text": {"maxCharacters": min(self.max_content_chars, 3000)}},
        }
        for key in ("includeDomains", "excludeDomains", "startPublishedDate", "endPublishedDate"):
            value = arguments.get(key)
            if value:
                payload[key] = value
        data, failure = self._post_json(
            f"{self.base_url}/search",
            headers=self._headers(),
            payload=payload,
            tool_name=local_name,
        )
        if failure:
            return failure
        results = [_normalize_exa_result(item, self.max_content_chars) for item in _exa_results(data or {})[:num_results]]
        return BridgeToolResult(
            success=True,
            output={"query": query, "results": results, "raw": data},
            output_summary=f"Exa search returned {len(results)} result(s).",
            metadata={**self._metadata(local_name, data_source="real_api"), "evidence_role": "discovery"},
        )

    def _fetch(self, arguments: dict[str, Any]) -> BridgeToolResult:
        urls = arguments.get("urls")
        if not isinstance(urls, list):
            single = str(arguments.get("url") or "").strip()
            urls = [single] if single else []
        urls = [str(url).strip() for url in urls if str(url).strip()][: self.max_results]
        if not urls:
            return self._failure("web_fetch_exa", "Missing required argument: url or urls", error_type="invalid_arguments")
        payload = {"urls": urls, "text": True, "summary": True}
        data, failure = self._post_json(
            f"{self.base_url}/contents",
            headers=self._headers(),
            payload=payload,
            tool_name="web_fetch_exa",
        )
        if failure:
            return failure
        results = [_normalize_exa_result(item, self.max_content_chars) for item in _exa_results(data or {})]
        return BridgeToolResult(
            success=True,
            output={"results": results, "raw": data},
            output_summary=f"Exa contents returned {len(results)} page(s).",
            metadata={**self._metadata("web_fetch_exa", data_source="real_api"), "evidence_role": "support"},
        )

    def _fake(self, local_name: str, arguments: dict[str, Any]) -> BridgeToolResult:
        if local_name in {"web_search_exa", "web_search_advanced_exa"}:
            query = str(arguments.get("query") or "semantic research")
            return BridgeToolResult(
                success=True,
                output={
                    "query": query,
                    "results": [
                        {
                            "title": "Exa fake semantic source",
                            "url": "https://example.com/exa/semantic-source",
                            "content": f"Semantically relevant fake result for {query}.",
                        }
                    ],
                },
                output_summary="Exa fake search returned 1 result.",
                metadata={**self._metadata(local_name, data_source="fake"), "evidence_role": "discovery"},
            )
        if local_name == "web_fetch_exa":
            url = str(arguments.get("url") or "https://example.com/exa/semantic-source")
            return BridgeToolResult(
                success=True,
                output={
                    "results": [
                        {
                            "title": "Exa fake fetched page",
                            "url": url,
                            "content": "Fetched fake page contents from Exa Contents for source-pack smoke coverage.",
                        }
                    ]
                },
                output_summary="Exa fake contents returned 1 page.",
                metadata={**self._metadata(local_name, data_source="fake"), "evidence_role": "support"},
            )
        return self._failure(local_name, f"Unknown Exa fake tool: {local_name}", error_type="unknown_tool")


def _exa_results(data: dict[str, Any]) -> list[dict[str, Any]]:
    results = data.get("results")
    if isinstance(results, list):
        return [item for item in results if isinstance(item, dict)]
    return []


def _normalize_exa_result(item: dict[str, Any], max_chars: int) -> dict[str, Any]:
    highlights = item.get("highlights") if isinstance(item.get("highlights"), list) else []
    content = (
        item.get("text")
        or item.get("summary")
        or " ".join(str(part) for part in highlights[:3])
        or item.get("description")
    )
    return {
        "title": item.get("title") or item.get("url") or item.get("id"),
        "url": item.get("url") or item.get("id"),
        "content": trim_text(content, max_chars),
        "publishedDate": item.get("publishedDate"),
        "author": item.get("author"),
        "score": item.get("score"),
    }

