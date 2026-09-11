"""URL normalization without trusting page-provided canonical hints."""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit


TRACKING_PARAMETERS = {
    "fbclid",
    "gclid",
    "dclid",
    "msclkid",
    "mc_cid",
    "mc_eid",
    "igshid",
    "yclid",
    "vero_conv",
    "vero_id",
}


@dataclass(frozen=True)
class CanonicalUrl:
    raw_url: str
    normalized_url: str
    fragment_locator: str | None


def canonicalize_url(url: str) -> CanonicalUrl:
    """Return a stable identity URL without making transport or safety decisions.

    The normalized value is suitable for cache keys, deduplication, lineage,
    and display. Callers must never replace the URL sent over the network with
    this identity projection.
    """
    raw = str(url or "").strip()
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError:
        return CanonicalUrl(raw, raw, None)
    scheme = parsed.scheme.casefold()
    hostname = (parsed.hostname or "").casefold().rstrip(".")
    if (
        scheme not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return CanonicalUrl(raw, raw, parsed.fragment or None)
    netloc = f"[{hostname}]" if ":" in hostname else hostname
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{netloc}:{port}"
    path = parsed.path or "/"
    normalized_path = posixpath.normpath(path)
    normalized_path = "/" + normalized_path.lstrip("/")
    if path.endswith("/") and normalized_path != "/":
        normalized_path += "/"
    if normalized_path != "/":
        normalized_path = normalized_path.rstrip("/")
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_") and key.casefold() not in TRACKING_PARAMETERS
    ]
    query.sort(key=lambda item: (item[0].casefold(), item[1]))
    normalized = urlunsplit((scheme, netloc, normalized_path, urlencode(query, doseq=True), ""))
    return CanonicalUrl(raw, normalized, parsed.fragment or None)


def resolve_canonical_hint(page_url: str, hint: str | None) -> str | None:
    candidate = str(hint or "").strip()
    if not candidate:
        return None
    resolved = urljoin(page_url, candidate)
    canonical = canonicalize_url(resolved).normalized_url
    parsed = urlsplit(canonical)
    return canonical if parsed.scheme in {"http", "https"} and parsed.hostname else None
