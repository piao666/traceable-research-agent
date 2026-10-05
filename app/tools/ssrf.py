"""Shared SSRF-safe URL validation for outbound fetch tools.

Consolidates the private/reserved network policy that was previously
duplicated (and incomplete) in ``web_fetcher`` and ``pdf_reader``. In
addition to literal IPs it covers integer/hex/octal IP shorthand and
resolves hostnames so DNS-rebinding to a private address is rejected
before a request is sent.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from pathlib import Path
from urllib.parse import urlparse, urlsplit

# Comprehensive private / reserved ranges (IPv4 + IPv6).
BLOCKED_NETWORKS = (
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("240.0.0.0/4"),
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("::ffff:0:0/96"),
    ipaddress.ip_network("64:ff9b::/96"),
    ipaddress.ip_network("100::/64"),
    ipaddress.ip_network("2001:db8::/32"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("ff00::/8"),
)

# RFC 2544 benchmarking space is commonly used by Fake-IP proxy DNS
# implementations.  It remains blocked for literal URLs and by default.  A
# caller may opt into it only after proving that the request's effective
# HTTP(S) proxy is an explicitly configured loopback proxy.
FAKE_IP_NETWORKS = (ipaddress.ip_network("198.18.0.0/15"),)
DOCKER_HOST_PROXY_NAME = "host.docker.internal"


def _is_fake_ip(address: object) -> bool:
    return any(address in net for net in FAKE_IP_NETWORKS)


def _is_blocked_ip(address: object, *, allow_fake_ip: bool = False) -> bool:
    if allow_fake_ip and _is_fake_ip(address):
        return False
    return any(address in net for net in BLOCKED_NETWORKS)


def _parse_dotted_ipv4(host: str) -> ipaddress.IPv4Address | None:
    """Parse a dotted-quad where each octet may be decimal/hex/octal.

    Handles obfuscations like ``0177.0.0.1`` (octal 127) and
    ``0x7f.0.0.1`` that ``ipaddress.ip_address`` rejects for ambiguity.
    """
    parts = host.split(".")
    if len(parts) != 4:
        return None
    octets: list[int] = []
    for part in parts:
        if not part:
            return None
        try:
            if part[:2].lower() == "0x":
                value = int(part, 16)
            elif len(part) > 1 and part[0] == "0":
                value = int(part, 8)
            else:
                value = int(part, 10)
        except ValueError:
            return None
        if not 0 <= value <= 255:
            return None
        octets.append(value)
    return ipaddress.IPv4Address(".".join(str(octet) for octet in octets))


def _check_numeric_host(host: str) -> bool | None:
    """Check decimal / hex / octal IP shorthand.

    Returns True/False when ``host`` is a numeric IP shorthand, or None when
    it is not numeric (and therefore should be treated as a hostname).
    """
    if host.isdigit():
        blocked = False
        # Try both decimal and octal interpretation (e.g. "0177").
        for base in (10, 8):
            try:
                if _is_blocked_ip(ipaddress.ip_address(int(host, base))):
                    blocked = True
            except ValueError:
                continue
        return blocked
    if len(host) > 2 and host[:2].lower() == "0x":
        try:
            return _is_blocked_ip(ipaddress.ip_address(int(host, 16)))
        except ValueError:
            return False
    return None


def is_blocked_host(host: str, *, allow_fake_ip: bool = False) -> bool:
    """Return True when a host is (or resolves to) a blocked address.

    ``allow_fake_ip`` only affects DNS answers.  Literal and obfuscated IP
    URLs are always checked against the complete blocked-network policy so a
    caller cannot turn the proxy compatibility mode into a private-IP bypass.
    """
    if not host:
        return True
    host = host.split("%")[0].strip("[]")

    # 1. Standard IP literal (IPv4 / IPv6).
    try:
        return _is_blocked_ip(ipaddress.ip_address(host))
    except ValueError:
        pass

    # 2. Obfuscated dotted-quad (per-octet octal/hex).
    dotted = _parse_dotted_ipv4(host)
    if dotted is not None:
        return _is_blocked_ip(dotted)

    # 3. Obfuscated integer IP shorthand (decimal/hex/octal).
    numeric = _check_numeric_host(host)
    if numeric is not None:
        return numeric

    # 3. Hostname: resolve and block if ANY address is private/reserved.
    #    Fail open on resolution errors so offline/mock flows still proceed
    #    (httpx surfaces the connection error itself in those cases).
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    for info in infos:
        try:
            address = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if _is_blocked_ip(address, allow_fake_ip=allow_fake_ip):
            return True
    return False


def validate_url(
    raw: str,
    *,
    trusted_local_proxy_url: str | None = None,
    proxy_environment: dict[str, str] | None = None,
) -> str | None:
    """Return a normalized safe URL, optionally accepting proxy Fake-IP DNS.

    Fake-IP acceptance is deliberately capability-based rather than a global
    exception: the caller must provide the exact expected proxy URL, that URL
    must be loopback, and the matching HTTP(S)/ALL_PROXY environment value
    must point to it.  Without all of those conditions the normal strict
    policy applies.
    """
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        return None
    host = (parsed.hostname or "").lower()
    allow_fake_ip = _trusted_proxy_for_request(
        parsed.scheme,
        host,
        trusted_local_proxy_url,
        proxy_environment,
    )
    if not host or is_blocked_host(host, allow_fake_ip=allow_fake_ip):
        return None
    return parsed.geturl()


def _trusted_proxy_for_request(
    scheme: str,
    host: str,
    trusted_local_proxy_url: str | None,
    proxy_environment: dict[str, str] | None,
) -> bool:
    """Verify that this request will use the configured local proxy."""
    if not trusted_local_proxy_url or _host_in_no_proxy(host, proxy_environment):
        return False
    expected = _normalize_proxy_url(trusted_local_proxy_url)
    if expected is None or not _is_trusted_proxy(expected):
        return False
    env = proxy_environment if proxy_environment is not None else dict(os.environ)
    values = {str(key).lower(): str(value).strip() for key, value in env.items() if value}
    key = f"{scheme.lower()}_proxy"
    actual = values.get(key) or values.get("all_proxy")
    if not actual:
        return False
    return _normalize_proxy_url(actual) == expected


def _normalize_proxy_url(
    value: str,
) -> tuple[str, str, int | None, str | None, str | None] | None:
    try:
        parsed = urlsplit(str(value).strip())
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return None
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            return None
        return (
            parsed.scheme.lower(),
            parsed.hostname.lower().strip("[]"),
            parsed.port,
            parsed.username,
            parsed.password,
        )
    except (TypeError, ValueError):
        return None


def _is_loopback_proxy(
    proxy: tuple[str, str, int | None, str | None, str | None],
) -> bool:
    try:
        address = ipaddress.ip_address(proxy[1])
    except ValueError:
        # Do not resolve proxy hostnames here: only a literal loopback
        # address is sufficiently explicit to grant the Fake-IP exception.
        return False
    return address.is_loopback


def _running_in_docker() -> bool:
    return Path("/.dockerenv").exists()


def _is_trusted_proxy(
    proxy: tuple[str, str, int | None, str | None, str | None],
) -> bool:
    if _is_loopback_proxy(proxy):
        return True
    # Docker Desktop exposes the host through this DNS name.  Accept it only
    # from an actual container and only when the operator configured the exact
    # proxy URL; arbitrary private/remote proxy hosts remain ineligible.
    return _running_in_docker() and proxy[1] == DOCKER_HOST_PROXY_NAME


def is_trusted_proxy_url(value: str | None) -> bool:
    """Return whether *value* is an eligible explicit local proxy URL."""
    normalized = _normalize_proxy_url(value) if value else None
    return normalized is not None and _is_trusted_proxy(normalized)


def _host_in_no_proxy(host: str, environment: dict[str, str] | None) -> bool:
    env = environment if environment is not None else dict(os.environ)
    values = {str(key).lower(): str(value).strip() for key, value in env.items() if value}
    raw = values.get("no_proxy")
    if not raw:
        return False
    hostname = host.casefold().strip("[]")
    for item in raw.split(","):
        token = item.strip().casefold()
        if not token:
            continue
        if token == "*":
            return True
        token = token.rsplit(":", 1)[0].strip("[]")
        if token.startswith("."):
            token = token[1:]
        if hostname == token or hostname.endswith("." + token):
            return True
    return False
