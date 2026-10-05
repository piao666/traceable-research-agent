"""Regression tests for explicit local-proxy Fake-IP SSRF compatibility."""

from unittest.mock import patch

import httpx

from app.retrieval.contracts import FetchFailureCode, FetchRequest
from app.retrieval.http_backend import HttpBackend
from app.config import Settings
from app.tools.ssrf import validate_url
from app.tools.web_fetcher import _build_router


FAKE_DNS = [(2, 1, 6, "", ("198.18.0.40", 443))]
PUBLIC_DNS = [(2, 1, 6, "", ("93.184.216.34", 443))]
LOCAL_PROXY = "http://127.0.0.1:7890"


def test_fake_ip_hostname_requires_explicit_matching_loopback_proxy() -> None:
    environment = {"https_proxy": LOCAL_PROXY}
    with patch("app.tools.ssrf.socket.getaddrinfo", return_value=FAKE_DNS):
        assert validate_url("https://example.com", proxy_environment=environment) is None
        assert validate_url(
            "https://example.com",
            trusted_local_proxy_url=LOCAL_PROXY,
            proxy_environment=environment,
        ) == "https://example.com"
        assert validate_url(
            "https://example.com",
            trusted_local_proxy_url="http://10.0.0.2:7890",
            proxy_environment=environment,
        ) is None
        assert validate_url(
            "https://example.com",
            trusted_local_proxy_url=LOCAL_PROXY,
            proxy_environment={"https_proxy": "http://10.0.0.2:7890"},
        ) is None


def test_fake_ip_requires_proxy_for_target_scheme_and_literal_fake_ip_stays_blocked() -> None:
    with patch("app.tools.ssrf.socket.getaddrinfo", return_value=FAKE_DNS):
        assert validate_url(
            "https://example.com",
            trusted_local_proxy_url=LOCAL_PROXY,
            proxy_environment={"http_proxy": LOCAL_PROXY},
        ) is None


def test_docker_host_proxy_is_trusted_only_inside_container() -> None:
    docker_proxy = "http://host.docker.internal:7897"
    environment = {"https_proxy": docker_proxy}
    with patch("app.tools.ssrf.socket.getaddrinfo", return_value=FAKE_DNS), patch(
        "app.tools.ssrf._running_in_docker", return_value=False
    ):
        assert validate_url(
            "https://example.com",
            trusted_local_proxy_url=docker_proxy,
            proxy_environment=environment,
        ) is None
    with patch("app.tools.ssrf.socket.getaddrinfo", return_value=FAKE_DNS), patch(
        "app.tools.ssrf._running_in_docker", return_value=True
    ):
        assert validate_url(
            "https://example.com",
            trusted_local_proxy_url=docker_proxy,
            proxy_environment=environment,
        ) == "https://example.com"
        assert validate_url(
            "https://198.18.0.40",
            trusted_local_proxy_url=LOCAL_PROXY,
            proxy_environment={"https_proxy": LOCAL_PROXY},
        ) is None


def test_fake_ip_mode_does_not_allow_mixed_private_dns_answers() -> None:
    mixed = FAKE_DNS + [(2, 1, 6, "", ("169.254.169.254", 443))]
    with patch("app.tools.ssrf.socket.getaddrinfo", return_value=mixed):
        assert validate_url(
            "https://example.com",
            trusted_local_proxy_url=LOCAL_PROXY,
            proxy_environment={"https_proxy": LOCAL_PROXY},
        ) is None


def test_http_backend_redirect_still_blocks_private_target_in_proxy_mode() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"location": "http://127.0.0.1/private"},
            request=request,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    backend = HttpBackend(
        client=client,
        cache_enabled=False,
        ssrf_trusted_local_proxy_enabled=True,
        ssrf_trusted_local_proxy_url=LOCAL_PROXY,
    )
    with patch.dict("os.environ", {"HTTPS_PROXY": LOCAL_PROXY}, clear=False), patch(
        "app.tools.ssrf.socket.getaddrinfo", return_value=PUBLIC_DNS
    ):
        result = backend.fetch(FetchRequest(url="https://example.com", max_chars=1000))
    assert result.failure is not None
    assert result.failure.code == FetchFailureCode.SSRF_BLOCKED


def test_http_backend_uses_explicit_proxy_mode_for_fake_ip_hostname() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(
            200,
            text="A sufficiently detailed public source body for testing. " * 20,
            headers={"content-type": "text/plain"},
            request=request,
        )

    backend = HttpBackend(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        cache_enabled=False,
        quality_min_score=0.0,
        ssrf_trusted_local_proxy_enabled=True,
        ssrf_trusted_local_proxy_url=LOCAL_PROXY,
    )
    with patch.dict("os.environ", {"HTTPS_PROXY": LOCAL_PROXY}, clear=False), patch(
        "app.tools.ssrf.socket.getaddrinfo", return_value=FAKE_DNS
    ):
        result = backend.fetch(FetchRequest(url="https://example.com/article", max_chars=1000))
    assert result.usable
    assert calls == ["https://example.com/article"]


def test_owned_http_client_scopes_proxy_without_global_proxy_environment() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(
            200,
            text=("Detailed public source content for scoped proxy transport validation. " * 8),
            headers={"content-type": "text/plain"},
            request=request,
        )

    transport_client = httpx.Client(transport=httpx.MockTransport(handler))
    backend = HttpBackend(
        cache_enabled=False,
        quality_min_score=0.0,
        ssrf_trusted_local_proxy_enabled=True,
        ssrf_trusted_local_proxy_url=LOCAL_PROXY,
    )
    with patch.dict("os.environ", {}, clear=True), patch(
        "app.tools.ssrf.socket.getaddrinfo", return_value=FAKE_DNS
    ), patch("app.retrieval.http_backend.httpx.Client", return_value=transport_client) as client_factory:
        result = backend.fetch(FetchRequest(url="https://example.com/scoped", max_chars=2000))

    assert result.usable
    assert calls == ["https://example.com/scoped"]
    assert client_factory.call_args.kwargs["proxy"] == LOCAL_PROXY


def test_settings_propagate_trusted_proxy_mode_to_http_backend() -> None:
    settings = Settings(
        evidence_reasoning_enabled=False,
        ssrf_trusted_local_proxy_enabled=True,
        ssrf_trusted_local_proxy_url=LOCAL_PROXY,
    )
    router = _build_router(settings, client=None, cache=None)
    assert router.http_backend.ssrf_trusted_local_proxy_url == LOCAL_PROXY
