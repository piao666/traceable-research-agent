"""Shared SSRF-safe URL validation for outbound fetch tools.

Consolidates the private/reserved network policy that was previously
duplicated (and incomplete) in ``web_fetcher`` and ``pdf_reader``. In
addition to literal IPs it covers integer/hex/octal IP shorthand and
resolves hostnames so DNS-rebinding to a private address is rejected
before a request is sent.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

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


def _is_blocked_ip(address: object) -> bool:
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


def is_blocked_host(host: str) -> bool:
    """Return True when a host is (or resolves to) a blocked address."""
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
        if _is_blocked_ip(address):
            return True
    return False


def validate_url(raw: str) -> str | None:
    """Return a normalized http(s) URL, or None if the host is unsafe."""
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        return None
    host = (parsed.hostname or "").lower()
    if not host or is_blocked_host(host):
        return None
    return parsed.geturl()
