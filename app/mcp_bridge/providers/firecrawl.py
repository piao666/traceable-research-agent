"""Firecrawl Source Pack provider."""

from __future__ import annotations

import os
from typing import Any

from app.mcp_bridge.providers.base import SourcePackProvider, trim_text
from app.mcp_bridge.schemas import BridgeTool, BridgeToolResult, json_schema


class FirecrawlProvider(SourcePackProvider):
    name = "firecrawl"

    @property
    def api_key(self) -> str:
        return os.getenv("FIRECRAWL_API_KEY", "").strip()

    @property
    def base_url(self) -> str:
        return os.getenv("FIRECRAWL_BASE_URL", "https://api.firecrawl.dev").strip().rstrip("/")

    def list_tools(self) -> list[BridgeTool]:
        return [
            BridgeTool(
                name="firecrawl.search",
                description="Search the web with Firecrawl and return discovery results.",
                input_schema=json_schema(
                    {
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "default": self.max_results},
                    },
                    ["query"],
                ),
                tags=["firecrawl", "search", "discovery"],
            ),
            BridgeTool(
                name="firecrawl.scrape",
                description="Scrape one URL with Firecrawl and return readable page content.",
                input_schema=json_schema(
                    {
                        "url": {"type": "string"},
                        "formats": {"type": "array", "items": {"type": "string"}, "default": ["markdown"]},
                    },
                    ["url"],
                ),
                tags=["firecrawl", "scrape", "support"],
            ),
            BridgeTool(
                name="firecrawl.map",
                description="Map a site with Firecrawl and return relevant URLs.",
                input_schema=json_schema(
                    {
                        "url": {"type": "string"},
                        "search": {"type": "string"},
                        "limit": {"type": "integer", "default": self.max_results},
                    },
                    ["url"],
                ),
                tags=["firecrawl", "map", "discovery"],
            ),
            BridgeTool(
                name="firecrawl.extract",
                description="Submit a read-only Firecrawl extraction request for one or more URLs.",
                input_schema=json_schema(
                    {
                        "url": {"type": "string"},
                        "urls": {"type": "array", "items": {"type": "string"}},
                        "prompt": {"type": "string"},
                        "schema": {"type": "object"},
                    },
                ),
                tags=["firecrawl", "extract", "support"],
            ),
        ]

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> BridgeToolResult:
        local_name = tool_name.split(".", 1)[-1]
        if self.fake_mode:
            return self._fake(local_name, arguments)
        if not self.api_key:
            return self._failure(local_name, "FIRECRAWL_API_KEY is not configured.", error_type="missing_api_key")
        if local_name == "search":
            return self._search(arguments)
        if local_name == "scrape":
            return self._scrape(arguments)
        if local_name == "map":
            return self._map(arguments)
        if local_name == "extract":
            return self._extract(arguments)
        return self._failure(local_name, f"Unknown Firecrawl tool: {tool_name}", error_type="unknown_tool")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _search(self, arguments: dict[str, Any]) -> BridgeToolResult:
        query = str(arguments.get("query") or "").strip()
        if not query:
            return self._failure("search", "Missing required argument: query", error_type="invalid_arguments")
        limit = min(int(arguments.get("limit") or self.max_results), self.max_results)
        payload = {"query": query[:500], "limit": limit}
        data, failure = self._post_json(
            f"{self.base_url}/v2/search",
            headers=self._headers(),
            payload=payload,
            tool_name="search",
        )
        if failure:
            return failure
        raw_results = _firecrawl_search_results(data or {})
        results = [_normalize_result(item, self.max_content_chars) for item in raw_results[:limit]]
        return BridgeToolResult(
            success=bool((data or {}).get("success", True)),
            output={"query": query, "results": results, "raw": data},
            output_summary=f"Firecrawl search returned {len(results)} result(s).",
            metadata={**self._metadata("search", data_source="real_api"), "evidence_role": "discovery"},
        )

    def _scrape(self, arguments: dict[str, Any]) -> BridgeToolResult:
        url = str(arguments.get("url") or "").strip()
        if not url:
            return self._failure("scrape", "Missing required argument: url", error_type="invalid_arguments")
        payload = {
            "url": url,
            "formats": arguments.get("formats") or ["markdown"],
            "onlyMainContent": True,
            "timeout": int(self.timeout_seconds * 1000),
            "removeBase64Images": True,
            "blockAds": True,
        }
        data, failure = self._post_json(
            f"{self.base_url}/v2/scrape",
            headers=self._headers(),
            payload=payload,
            tool_name="scrape",
        )
        if failure:
            return failure
        page = (data or {}).get("data") if isinstance((data or {}).get("data"), dict) else {}
        metadata = page.get("metadata") if isinstance(page.get("metadata"), dict) else {}
        output = {
            "title": metadata.get("title") or page.get("title") or url,
            "url": metadata.get("sourceURL") or metadata.get("url") or url,
            "markdown": trim_text(page.get("markdown") or page.get("summary") or page.get("html"), self.max_content_chars),
            "links": page.get("links") or [],
            "metadata": metadata,
            "raw": data,
        }
        return BridgeToolResult(
            success=bool((data or {}).get("success", True)),
            output=output,
            output_summary=f"Firecrawl scraped {output['url']}.",
            metadata={**self._metadata("scrape", data_source="real_api"), "evidence_role": "support"},
        )

    def _map(self, arguments: dict[str, Any]) -> BridgeToolResult:
        url = str(arguments.get("url") or "").strip()
        if not url:
            return self._failure("map", "Missing required argument: url", error_type="invalid_arguments")
        limit = min(int(arguments.get("limit") or self.max_results), self.max_results)
        payload = {
            "url": url,
            "search": str(arguments.get("search") or "").strip() or None,
            "limit": limit,
            "ignoreQueryParameters": True,
            "timeout": int(self.timeout_seconds * 1000),
        }
        payload = {key: value for key, value in payload.items() if value is not None}
        data, failure = self._post_json(
            f"{self.base_url}/v2/map",
            headers=self._headers(),
            payload=payload,
            tool_name="map",
        )
        if failure:
            return failure
        links = (data or {}).get("links") if isinstance((data or {}).get("links"), list) else []
        results = [_normalize_result(item, self.max_content_chars) for item in links[:limit] if isinstance(item, dict)]
        return BridgeToolResult(
            success=bool((data or {}).get("success", True)),
            output={"url": url, "results": results, "raw": data},
            output_summary=f"Firecrawl map returned {len(results)} link(s).",
            metadata={**self._metadata("map", data_source="real_api"), "evidence_role": "discovery"},
        )

    def _extract(self, arguments: dict[str, Any]) -> BridgeToolResult:
        urls = arguments.get("urls")
        if not isinstance(urls, list):
            single = str(arguments.get("url") or "").strip()
            urls = [single] if single else []
        urls = [str(url).strip() for url in urls if str(url).strip()][: self.max_results]
        if not urls:
            return self._failure("extract", "Missing required argument: url or urls", error_type="invalid_arguments")
        payload = {
            "urls": urls,
            "prompt": str(arguments.get("prompt") or "Extract key facts and source evidence from the provided pages."),
            "schema": arguments.get("schema") if isinstance(arguments.get("schema"), dict) else {},
            "showSources": True,
            "ignoreInvalidURLs": True,
            "scrapeOptions": {"formats": ["markdown"], "onlyMainContent": True},
        }
        data, failure = self._post_json(
            f"{self.base_url}/v2/extract",
            headers=self._headers(),
            payload=payload,
            tool_name="extract",
        )
        if failure:
            return failure
        return BridgeToolResult(
            success=bool((data or {}).get("success", True)),
            output={"urls": urls, "content": trim_text(data, self.max_content_chars), "raw": data},
            output_summary=f"Firecrawl extract submitted for {len(urls)} URL(s).",
            metadata={**self._metadata("extract", data_source="real_api"), "evidence_role": "support"},
        )

    def _fake(self, local_name: str, arguments: dict[str, Any]) -> BridgeToolResult:
        metadata = {**self._metadata(local_name, data_source="fake"), "evidence_role": "support"}
        if local_name == "search":
            query = str(arguments.get("query") or "deep research")
            metadata["evidence_role"] = "discovery"
            return BridgeToolResult(
                success=True,
                output={
                    "query": query,
                    "results": [
                        {
                            "title": "Firecrawl fake source: readable web evidence",
                            "url": "https://example.com/firecrawl/source-pack",
                            "content": f"Discovery result for {query}.",
                        }
                    ],
                },
                output_summary="Firecrawl fake search returned 1 result.",
                metadata=metadata,
            )
        if local_name == "map":
            metadata["evidence_role"] = "discovery"
            return BridgeToolResult(
                success=True,
                output={
                    "url": arguments.get("url") or "https://example.com",
                    "results": [
                        {
                            "title": "Firecrawl fake mapped page",
                            "url": "https://example.com/firecrawl/mapped-page",
                            "content": "Mapped page relevant to the research topic.",
                        }
                    ],
                },
                output_summary="Firecrawl fake map returned 1 link.",
                metadata=metadata,
            )
        if local_name == "scrape":
            url = str(arguments.get("url") or "https://example.com/firecrawl/source-pack")
            return BridgeToolResult(
                success=True,
                output={
                    "title": "Firecrawl fake scraped page",
                    "url": url,
                    "markdown": "This fake scraped page contains readable body content, source context, and evidence snippets for the report.",
                    "metadata": {"title": "Firecrawl fake scraped page", "sourceURL": url},
                },
                output_summary=f"Firecrawl fake scrape returned readable content for {url}.",
                metadata=metadata,
            )
        if local_name == "extract":
            return BridgeToolResult(
                success=True,
                output={
                    "title": "Firecrawl fake extraction",
                    "url": (arguments.get("url") or "https://example.com/firecrawl/source-pack"),
                    "content": "Extracted fake structured facts with source attribution.",
                },
                output_summary="Firecrawl fake extract returned structured evidence.",
                metadata=metadata,
            )
        return self._failure(local_name, f"Unknown Firecrawl fake tool: {local_name}", error_type="unknown_tool")


def _firecrawl_search_results(data: dict[str, Any]) -> list[dict[str, Any]]:
    body = data.get("data")
    if isinstance(body, list):
        return [item for item in body if isinstance(item, dict)]
    if isinstance(body, dict):
        web = body.get("web")
        if isinstance(web, list):
            return [item for item in web if isinstance(item, dict)]
    results = data.get("results")
    if isinstance(results, list):
        return [item for item in results if isinstance(item, dict)]
    return []


def _normalize_result(item: dict[str, Any], max_chars: int) -> dict[str, Any]:
    return {
        "title": item.get("title") or item.get("name") or item.get("url"),
        "url": item.get("url") or item.get("sourceURL") or item.get("sourceUrl"),
        "content": trim_text(
            item.get("markdown")
            or item.get("content")
            or item.get("description")
            or item.get("summary"),
            max_chars,
        ),
        "score": item.get("score"),
    }

