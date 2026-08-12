"""Deterministic FetchCache and web_fetcher cache integration tests."""

from __future__ import annotations

import hashlib
import tempfile
import time
import unittest
from pathlib import Path

import httpx

from app.config import Settings
from app.tools.fetch_cache import FetchCache, FetchCacheEntry
from app.tools.web_fetcher import web_fetch


HTML_ONE = "<html><title>One</title><body><main>" + ("Primary cached content. " * 12) + "</main></body></html>"
HTML_TWO = "<html><title>Two</title><body><main>" + ("Fresh replacement content. " * 12) + "</main></body></html>"
URL = "https://example.com/research"


class WebFetcherCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.cache = FetchCache(self.temp_dir.name, default_ttl=60)
        self.settings = Settings(
            web_fetcher_cache_enabled=True,
            web_fetcher_cache_dir=self.temp_dir.name,
            web_fetcher_cache_ttl_seconds=60,
            web_fetcher_trafilatura_enabled=False,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _client(self, handler) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler))

    def _fetch(self, client: httpx.Client, cache: FetchCache | None = None):
        return web_fetch(
            {"urls": [URL], "max_chars": 2000},
            settings_obj=self.settings,
            cache=cache or self.cache,
            client=client,
        )

    def _expire_entry(self) -> None:
        cache_key = next(iter(self.cache._index))
        self.cache._index[cache_key]["fetched_at"] = time.time() - 120
        self.cache._index[cache_key]["ttl_seconds"] = 1
        self.cache._save_index()

    def test_fresh_hit_skips_network_and_preserves_extraction(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, text=HTML_ONE, headers={"content-type": "text/html"})

        client = self._client(handler)
        first = self._fetch(client)
        second = self._fetch(client)

        self.assertEqual(calls, 1)
        self.assertEqual(first.output["pages"][0]["cache_status"], "miss")
        self.assertTrue(first.output["pages"][0]["cache_stored"])
        self.assertEqual(second.output["pages"][0]["cache_status"], "hit")
        self.assertTrue(second.output["pages"][0]["cache_hit"])
        self.assertEqual(second.metadata["cache_hits"], 1)
        self.assertEqual(second.output["pages"][0]["title"], "One")
        self.assertEqual(
            second.output["pages"][0]["extraction_method"],
            first.output["pages"][0]["extraction_method"],
        )

    def test_expired_entry_refetches_and_replaces_content(self) -> None:
        responses = [HTML_ONE, HTML_TWO]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                text=responses.pop(0),
                headers={"content-type": "text/html"},
            )

        client = self._client(handler)
        self._fetch(client)
        self._expire_entry()
        refreshed = self._fetch(client)

        page = refreshed.output["pages"][0]
        self.assertEqual(page["cache_status"], "expired")
        self.assertIn("Fresh replacement content", page["content"])
        self.assertEqual(refreshed.metadata["cache_expired"], 1)
        self.assertEqual(responses, [])

    def test_expired_etag_entry_revalidates_with_304(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx.Response(
                    200,
                    text=HTML_ONE,
                    headers={"content-type": "text/html", "etag": '"v1"'},
                )
            self.assertEqual(request.headers.get("if-none-match"), '"v1"')
            return httpx.Response(304)

        client = self._client(handler)
        self._fetch(client)
        self._expire_entry()
        revalidated = self._fetch(client)

        page = revalidated.output["pages"][0]
        self.assertEqual(calls, 2)
        self.assertEqual(page["cache_status"], "revalidated")
        self.assertTrue(page["cache_hit"])
        self.assertIn("Primary cached content", page["content"])
        self.assertEqual(revalidated.metadata["cache_revalidated"], 1)

    def test_disabled_cache_always_fetches_and_writes_nothing(self) -> None:
        calls = 0
        disabled = self.settings.model_copy(update={"web_fetcher_cache_enabled": False})

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, text=HTML_ONE, headers={"content-type": "text/html"})

        client = self._client(handler)
        for _ in range(2):
            result = web_fetch(
                {"urls": [URL]},
                settings_obj=disabled,
                cache=self.cache,
                client=client,
            )
            self.assertEqual(result.output["pages"][0]["cache_status"], "disabled")
        self.assertEqual(calls, 2)
        self.assertEqual(self.cache.stats()["total_entries"], 0)

    def test_corrupt_content_is_rejected_and_refetched(self) -> None:
        responses = [HTML_ONE, HTML_TWO]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=responses.pop(0), headers={"content-type": "text/html"})

        client = self._client(handler)
        self._fetch(client)
        content_hash = next(iter(self.cache._index.values()))["content_hash"]
        Path(self.temp_dir.name, f"{content_hash}.txt").write_text("tampered", encoding="utf-8")
        recovered = self._fetch(client)

        page = recovered.output["pages"][0]
        self.assertEqual(page["cache_status"], "corrupt")
        self.assertFalse(page["cache_hit"])
        self.assertIn("Fresh replacement content", page["content"])
        self.assertEqual(recovered.metadata["cache_corrupt"], 1)

    def test_failed_response_is_not_cached(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(503, text="unavailable")

        client = self._client(handler)
        first = self._fetch(client)
        second = self._fetch(client)
        self.assertEqual(calls, 2)
        self.assertEqual(first.output["failed_count"], 1)
        self.assertEqual(second.output["pages"][0]["cache_status"], "miss")
        self.assertEqual(self.cache.stats()["total_entries"], 0)

    def test_multiple_cache_instances_merge_index_updates(self) -> None:
        second_cache = FetchCache(self.temp_dir.name, default_ttl=60)

        def entry(cache: FetchCache, url: str, content: str) -> FetchCacheEntry:
            return FetchCacheEntry(
                cache_key=cache._compute_key(url),
                url=url,
                content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                content=content,
                content_type="text/html",
                fetched_at=time.time(),
                ttl_seconds=60,
            )

        self.assertTrue(self.cache.put(entry(self.cache, "https://example.com/a", "alpha")))
        self.assertTrue(second_cache.put(entry(second_cache, "https://example.com/b", "beta")))

        self.assertEqual(self.cache.lookup("https://example.com/a")[1], "hit")
        self.assertEqual(self.cache.lookup("https://example.com/b")[1], "hit")
        self.assertEqual(self.cache.stats()["total_entries"], 2)


if __name__ == "__main__":
    unittest.main()
