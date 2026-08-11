"""Reference existence gate — verifies that cited academic references are
resolvable through free open indexes (Crossref, OpenAlex, arXiv, Semantic Scholar).

Phase 8.4: Each reference with a DOI, arXiv ID, or title+author is checked
against free indexes. Results are classified as verified / probable / inconsistent
/ unresolved, and the verification report is appended to the report and persisted
in AgentRun metrics and trace events.

Runs in parallel with citation_validator (Phase 7.5):
  citation_validator   → "Does this passage support the report claim?"
  reference_verifier   → "Does this reference exist in a trusted index?"
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


# ── Rate limiting ────────────────────────────────────────────────────────

_RATE_LIMIT_LOCK = threading.Lock()
_LAST_REQUEST_AT = 0.0
_MIN_INTERVAL_S = 1.0  # polite: 1 request per second across indexes


def _respect_rate_limit() -> None:
    global _LAST_REQUEST_AT
    with _RATE_LIMIT_LOCK:
        now = time.monotonic()
        delay = _MIN_INTERVAL_S - (now - _LAST_REQUEST_AT)
        if delay > 0:
            time.sleep(delay)
        _LAST_REQUEST_AT = time.monotonic()


# ── Cache ─────────────────────────────────────────────────────────────────

@dataclass
class _CacheEntry:
    identifier: str
    identifier_type: str
    status: str
    data: dict[str, Any]
    cached_at: float


class _ReferenceCache:
    """Simple file-based cache for reference verification results."""

    def __init__(self, cache_dir: str, ttl_seconds: int = 86400) -> None:
        self.cache_dir = cache_dir
        self.ttl_seconds = ttl_seconds
        self._ensure_dir()

    def _ensure_dir(self) -> None:
        os.makedirs(self.cache_dir, exist_ok=True)

    def _cache_key(self, identifier: str, identifier_type: str) -> str:
        raw = f"{identifier_type}:{identifier.strip().lower()}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    def _cache_path(self, key: str) -> str:
        return os.path.join(self.cache_dir, f"{key}.json")

    def get(self, identifier: str, identifier_type: str) -> _CacheEntry | None:
        key = self._cache_key(identifier, identifier_type)
        path = self._cache_path(key)
        if not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (json.JSONDecodeError, OSError):
            return None
        entry = _CacheEntry(**raw)
        if time.monotonic() - entry.cached_at > self.ttl_seconds:
            try:
                os.remove(path)
            except OSError:
                pass
            return None
        return entry

    def put(self, identifier: str, identifier_type: str, status: str, data: dict[str, Any]) -> None:
        key = self._cache_key(identifier, identifier_type)
        path = self._cache_path(key)
        entry = _CacheEntry(
            identifier=identifier,
            identifier_type=identifier_type,
            status=status,
            data=data,
            cached_at=time.monotonic(),
        )
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(entry.__dict__, fh, ensure_ascii=False, default=str)
        except OSError:
            pass  # cache write failure is non-fatal


# ── Data models ───────────────────────────────────────────────────────────

@dataclass
class ReferenceVerificationDetail:
    """Per-reference verification result."""
    ref_label: str              # e.g. "REF-001" or auto-generated label
    identifier_type: str        # "doi" | "arxiv" | "title_author"
    identifier_value: str
    status: str                 # verified | probable | inconsistent | unresolved
    indexes_checked: list[str] = field(default_factory=list)
    matched_title: str | None = None
    matched_authors: list[str] = field(default_factory=list)
    matched_year: int | None = None
    matched_venue: str | None = None
    metadata_conflicts: list[str] = field(default_factory=list)
    failure_reason: str | None = None
    scores: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref_label": self.ref_label,
            "identifier_type": self.identifier_type,
            "identifier_value": self.identifier_value,
            "status": self.status,
            "indexes_checked": self.indexes_checked,
            "matched_title": self.matched_title,
            "matched_authors": self.matched_authors,
            "matched_year": self.matched_year,
            "matched_venue": self.matched_venue,
            "metadata_conflicts": self.metadata_conflicts,
            "failure_reason": self.failure_reason,
            "scores": self.scores,
        }


@dataclass
class ReferenceVerificationReport:
    """Aggregate verification report."""
    total: int = 0
    verified: int = 0
    probable: int = 0
    inconsistent: int = 0
    unresolved: int = 0
    details: list[ReferenceVerificationDetail] = field(default_factory=list)
    indexes_available: list[str] = field(default_factory=list)
    network_failures: int = 0

    @property
    def verification_rate(self) -> float:
        if self.total == 0:
            return 1.0
        return round(self.verified / self.total, 4)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "verified": self.verified,
            "probable": self.probable,
            "inconsistent": self.inconsistent,
            "unresolved": self.unresolved,
            "verification_rate": self.verification_rate,
            "indexes_available": self.indexes_available,
            "network_failures": self.network_failures,
            "details": [d.to_dict() for d in self.details],
        }


# ── Title similarity ──────────────────────────────────────────────────────

def _tokenize_title(title: str) -> set[str]:
    """Tokenize a title into lowercase alpha-numeric tokens for comparison."""
    lowered = title.lower()
    tokens = {
        token for token in re.findall(r"[a-z0-9]{2,}", lowered)
        if token not in {"the", "and", "for", "with", "from", "that", "this"}
    }
    return tokens


def _title_similarity(a: str, b: str) -> float:
    """Jaccard similarity between two titles."""
    tokens_a = _tokenize_title(a)
    tokens_b = _tokenize_title(b)
    if not tokens_a and not tokens_b:
        return 1.0
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union) if union else 0.0


def _authors_match(ref_authors: list[str], matched_authors: list[str]) -> bool:
    """Check if at least one author surname appears in both lists."""
    if not ref_authors or not matched_authors:
        return False
    ref_surnames = {a.strip().lower().split()[-1] for a in ref_authors if a.strip()}
    matched_surnames = {a.strip().lower().split()[-1] for a in matched_authors if a.strip()}
    return bool(ref_surnames & matched_surnames)


# ── HTTP helpers ──────────────────────────────────────────────────────────

def _http_get_json(url: str, timeout: int = 10) -> tuple[dict[str, Any] | None, str | None]:
    """Perform a GET request and return parsed JSON or (None, error_reason)."""
    _respect_rate_limit()
    try:
        req = Request(url, headers={"User-Agent": "TraceableResearchAgent/1.0 (mailto:research@localhost)"})
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return json.loads(raw.decode("utf-8")), None
    except HTTPError as exc:
        if exc.code == 404:
            return None, "not_found"
        return None, f"http_{exc.code}"
    except URLError:
        return None, "network_unavailable"
    except (json.JSONDecodeError, OSError, TimeoutError, ValueError):
        return None, "network_unavailable"


def _http_get_xml(url: str, timeout: int = 10) -> tuple[str | None, str | None]:
    """Perform a GET request and return raw XML text or (None, error_reason)."""
    _respect_rate_limit()
    try:
        req = Request(url, headers={"User-Agent": "TraceableResearchAgent/1.0 (mailto:research@localhost)"})
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return raw.decode("utf-8"), None
    except HTTPError as exc:
        if exc.code == 404:
            return None, "not_found"
        return None, f"http_{exc.code}"
    except URLError:
        return None, "network_unavailable"
    except (OSError, TimeoutError, ValueError):
        return None, "network_unavailable"


# ── Index checkers ────────────────────────────────────────────────────────

def _check_crossref_doi(doi: str, timeout: int, cache: _ReferenceCache | None) -> tuple[str, dict[str, Any], str | None]:
    """Check a DOI against the Crossref API.

    Returns: (status, match_data, failure_reason)
    """
    # Try cache
    if cache:
        entry = cache.get(doi, "doi")
        if entry:
            return entry.status, entry.data, entry.data.get("failure_reason")

    url = f"https://api.crossref.org/works/{quote(doi, safe='')}"
    data, error = _http_get_json(url, timeout=timeout)
    if error or not data:
        reason = error or "unknown"
        result = {"index": "crossref", "failure_reason": reason}
        if cache:
            cache.put(doi, "doi", "unresolved", result)
        return "unresolved", result, reason

    message = data.get("message") if isinstance(data.get("message"), dict) else {}
    if not message:
        result = {"index": "crossref", "failure_reason": "not_found"}
        if cache:
            cache.put(doi, "doi", "unresolved", result)
        return "unresolved", result, "not_found"

    title_list = message.get("title") or []
    matched_title = title_list[0] if title_list else None
    author_objs = message.get("author") or []
    matched_authors = [
        f"{a.get('given', '')} {a.get('family', '')}".strip()
        for a in author_objs if isinstance(a, dict)
    ]
    issued = message.get("issued") if isinstance(message.get("issued"), dict) else {}
    date_parts = issued.get("date-parts") or []
    matched_year = date_parts[0][0] if date_parts and date_parts[0] else None
    matched_venue = (message.get("container-title") or [None])[0]

    result = {
        "index": "crossref",
        "matched_title": matched_title,
        "matched_authors": matched_authors,
        "matched_year": matched_year,
        "matched_venue": matched_venue,
        "publisher": message.get("publisher"),
    }
    if cache:
        cache.put(doi, "doi", "verified", result)
    return "verified", result, None


def _check_openalex_doi(doi: str, timeout: int, cache: _ReferenceCache | None) -> tuple[str, dict[str, Any], str | None]:
    """Check a DOI against the OpenAlex API."""
    if cache:
        entry = cache.get(doi, "doi_openalex")
        if entry:
            return entry.status, entry.data, entry.data.get("failure_reason")

    url = f"https://api.openalex.org/works/doi:{quote(doi, safe='')}"
    data, error = _http_get_json(url, timeout=timeout)
    if error or not data:
        reason = error or "unknown"
        result = {"index": "openalex", "failure_reason": reason}
        if cache:
            cache.put(doi, "doi_openalex", "unresolved", result)
        return "unresolved", result, reason

    matched_title = data.get("title")
    authorship = data.get("authorships") or []
    matched_authors = [
        a.get("author", {}).get("display_name", "")
        for a in authorship if isinstance(a, dict)
    ]
    matched_year = data.get("publication_year")
    loc = data.get("primary_location") if isinstance(data.get("primary_location"), dict) else {}
    source = loc.get("source") if isinstance(loc.get("source"), dict) else {}
    matched_venue = source.get("display_name")

    result = {
        "index": "openalex",
        "matched_title": matched_title,
        "matched_authors": matched_authors,
        "matched_year": matched_year,
        "matched_venue": matched_venue,
        "openalex_id": data.get("id"),
        "cited_by_count": data.get("cited_by_count"),
    }
    if cache:
        cache.put(doi, "doi_openalex", "verified", result)
    return "verified", result, None


def _check_arxiv_id(arxiv_id: str, timeout: int, cache: _ReferenceCache | None) -> tuple[str, dict[str, Any], str | None]:
    """Check an arXiv ID against the arXiv API."""
    if cache:
        entry = cache.get(arxiv_id, "arxiv")
        if entry:
            return entry.status, entry.data, entry.data.get("failure_reason")

    cleaned = arxiv_id.strip().replace("arxiv:", "").replace("arXiv:", "")
    url = f"http://export.arxiv.org/api/query?id_list={quote(cleaned)}&max_results=1"
    raw_xml, error = _http_get_xml(url, timeout=timeout)
    if error or not raw_xml:
        reason = error or "unknown"
        result = {"index": "arxiv", "failure_reason": reason}
        if cache:
            cache.put(arxiv_id, "arxiv", "unresolved", result)
        return "unresolved", result, reason

    import xml.etree.ElementTree as ET
    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "arxiv": "http://arxiv.org/schemas/atom",
    }
    try:
        root = ET.fromstring(raw_xml)
    except ET.ParseError:
        result = {"index": "arxiv", "failure_reason": "parse_error"}
        return "unresolved", result, "parse_error"

    entry = root.find("atom:entry", ns)
    if entry is None:
        result = {"index": "arxiv", "failure_reason": "not_found"}
        if cache:
            cache.put(arxiv_id, "arxiv", "unresolved", result)
        return "unresolved", result, "not_found"

    title_el = entry.find("atom:title", ns)
    matched_title = (title_el.text or "").strip().replace("\n", " ") if title_el is not None else None
    author_els = entry.findall("atom:author/atom:name", ns)
    matched_authors = [el.text.strip() for el in author_els if el is not None and el.text]
    published = entry.find("atom:published", ns)
    matched_year = int((published.text or "")[:4]) if published is not None and published.text else None

    result = {
        "index": "arxiv",
        "matched_title": matched_title,
        "matched_authors": matched_authors,
        "matched_year": matched_year,
        "matched_venue": None,
        "arxiv_id": cleaned,
    }
    if cache:
        cache.put(arxiv_id, "arxiv", "verified", result)
    return "verified", result, None


def _check_semantic_scholar_title(
    title: str, timeout: int, cache: _ReferenceCache | None,
) -> tuple[str, dict[str, Any], str | None]:
    """Search Semantic Scholar by title."""
    title_key = hashlib.sha256(title.strip().lower().encode("utf-8")).hexdigest()[:16]
    if cache:
        entry = cache.get(title_key, "s2_title")
        if entry:
            return entry.status, entry.data, entry.data.get("failure_reason")

    url = f"https://api.semanticscholar.org/graph/v1/paper/search?{urlencode({'query': title[:300], 'limit': '3', 'fields': 'title,authors,year,venue,externalIds'})}"
    data, error = _http_get_json(url, timeout=timeout)
    if error or not data:
        reason = error or "unknown"
        result = {"index": "semantic_scholar", "failure_reason": reason}
        if cache:
            cache.put(title_key, "s2_title", "unresolved", result)
        return "unresolved", result, reason

    papers = data.get("data") if isinstance(data.get("data"), list) else []
    if not papers:
        result = {"index": "semantic_scholar", "failure_reason": "not_found"}
        if cache:
            cache.put(title_key, "s2_title", "unresolved", result)
        return "unresolved", result, "not_found"

    # Find best match by title similarity
    best = papers[0]
    best_sim = _title_similarity(title, best.get("title") or "")
    for p in papers[1:]:
        sim = _title_similarity(title, p.get("title") or "")
        if sim > best_sim:
            best_sim = sim
            best = p

    authors = [a.get("name", "") for a in best.get("authors") or []]
    external_ids = best.get("externalIds") or {}
    result = {
        "index": "semantic_scholar",
        "matched_title": best.get("title"),
        "matched_authors": authors,
        "matched_year": best.get("year"),
        "matched_venue": best.get("venue"),
        "s2_paper_id": best.get("paperId"),
        "external_ids": {
            "DOI": external_ids.get("DOI"),
            "ArXiv": external_ids.get("ArXiv"),
        },
        "title_similarity": round(best_sim, 4),
    }

    status = "verified" if best_sim >= 0.8 else "probable"
    if cache:
        cache.put(title_key, "s2_title", status, result)
    return status, result, None


# ── Main verifier ─────────────────────────────────────────────────────────

class ReferenceVerifier:
    """Verifies academic references against free open indexes."""

    def __init__(
        self,
        *,
        allowed_indexes: list[str] | None = None,
        timeout: int = 30,
        cache_dir: str = "workspace/cache/reference",
        cache_ttl: int = 86400,
    ) -> None:
        self.allowed_indexes = allowed_indexes or ["crossref", "openalex", "arxiv", "semantic_scholar"]
        self.timeout = timeout
        self.cache = _ReferenceCache(cache_dir, cache_ttl) if cache_dir else None

    def verify(self, references: list[dict[str, Any]]) -> ReferenceVerificationReport:
        """Verify a list of reference dicts.

        Each reference dict should have:
          document_id, title, authors[], year, venue, doi, arxiv_id
        """
        if not references:
            return ReferenceVerificationReport()

        details: list[ReferenceVerificationDetail] = []
        verified = probable = inconsistent = unresolved = 0
        network_failures = 0

        for i, ref in enumerate(references):
            doi = str(ref.get("doi") or "").strip()
            arxiv_id = str(ref.get("arxiv_id") or "").strip()
            title = str(ref.get("title") or "").strip()
            authors = ref.get("authors") or []
            year = ref.get("year")
            venue = ref.get("venue")

            ref_label = ref.get("label") or ref.get("document_id") or f"REF-{i + 1:03d}"

            detail = ReferenceVerificationDetail(
                ref_label=ref_label,
                identifier_type="unknown",
                identifier_value="",
                status="unresolved",
            )

            # Determine primary identifier
            if doi:
                detail.identifier_type = "doi"
                detail.identifier_value = doi
                detail = self._verify_by_doi(detail, doi, title, authors, year, venue)
            elif arxiv_id:
                detail.identifier_type = "arxiv"
                detail.identifier_value = arxiv_id
                detail = self._verify_by_arxiv(detail, arxiv_id, title, authors, year, venue)
            elif title:
                detail.identifier_type = "title_author"
                detail.identifier_value = title[:100]
                detail = self._verify_by_title(detail, title, authors, year, venue)
            else:
                detail.status = "unresolved"
                detail.failure_reason = "no_identifier"
                detail.indexes_checked = []

            # Cross-check metadata consistency
            if detail.status == "verified" and detail.matched_title and title:
                meta_conflicts = self._detect_metadata_conflicts(
                    ref_title=title,
                    ref_authors=authors,
                    ref_year=year,
                    ref_venue=venue,
                    matched_title=detail.matched_title,
                    matched_authors=detail.matched_authors,
                    matched_year=detail.matched_year,
                    matched_venue=detail.matched_venue,
                )
                if meta_conflicts:
                    detail.metadata_conflicts = meta_conflicts
                    detail.status = "inconsistent"

            # Track network failures
            if detail.failure_reason == "network_unavailable":
                network_failures += 1

            # Count
            if detail.status == "verified":
                verified += 1
            elif detail.status == "probable":
                probable += 1
            elif detail.status == "inconsistent":
                inconsistent += 1
            else:
                unresolved += 1

            details.append(detail)

        return ReferenceVerificationReport(
            total=len(references),
            verified=verified,
            probable=probable,
            inconsistent=inconsistent,
            unresolved=unresolved,
            details=details,
            indexes_available=self._available_indexes(),
            network_failures=network_failures,
        )

    def _verify_by_doi(
        self,
        detail: ReferenceVerificationDetail,
        doi: str,
        title: str,
        authors: list[str],
        year: Any,
        venue: Any,
    ) -> ReferenceVerificationDetail:
        results: list[tuple[str, str, dict[str, Any]]] = []

        if "crossref" in self.allowed_indexes:
            status, data, err = _check_crossref_doi(doi, self.timeout, self.cache)
            detail.indexes_checked.append("crossref")
            if status != "unresolved":
                results.append(("crossref", status, data))
            elif err:
                detail.scores["crossref"] = 0.0

        if "openalex" in self.allowed_indexes:
            status, data, err = _check_openalex_doi(doi, self.timeout, self.cache)
            detail.indexes_checked.append("openalex")
            if status != "unresolved":
                results.append(("openalex", status, data))
            elif err:
                detail.scores["openalex"] = 0.0

        return self._synthesize_verdict(detail, results)

    def _verify_by_arxiv(
        self,
        detail: ReferenceVerificationDetail,
        arxiv_id: str,
        title: str,
        authors: list[str],
        year: Any,
        venue: Any,
    ) -> ReferenceVerificationDetail:
        results: list[tuple[str, str, dict[str, Any]]] = []

        if "arxiv" in self.allowed_indexes:
            status, data, err = _check_arxiv_id(arxiv_id, self.timeout, self.cache)
            detail.indexes_checked.append("arxiv")
            if status != "unresolved":
                results.append(("arxiv", status, data))
            elif err:
                detail.scores["arxiv"] = 0.0

        return self._synthesize_verdict(detail, results)

    def _verify_by_title(
        self,
        detail: ReferenceVerificationDetail,
        title: str,
        authors: list[str],
        year: Any,
        venue: Any,
    ) -> ReferenceVerificationDetail:
        results: list[tuple[str, str, dict[str, Any]]] = []

        if "semantic_scholar" in self.allowed_indexes:
            status, data, err = _check_semantic_scholar_title(title, self.timeout, self.cache)
            detail.indexes_checked.append("semantic_scholar")
            if status != "unresolved":
                results.append(("semantic_scholar", status, data))
                detail.scores["title_similarity"] = data.get("title_similarity", 0.0)
            elif err:
                detail.scores["semantic_scholar"] = 0.0

        return self._synthesize_verdict(detail, results)

    def _synthesize_verdict(
        self,
        detail: ReferenceVerificationDetail,
        results: list[tuple[str, str, dict[str, Any]]],
    ) -> ReferenceVerificationDetail:
        """Combine results from multiple indexes into a single verdict."""
        if not results:
            # All indexes failed or returned nothing
            detail.status = "unresolved"
            detail.failure_reason = "all_indexes_failed"
            return detail

        verified_results = [(idx, data) for idx, status, data in results if status == "verified"]

        if len(verified_results) >= 2:
            # Two independent indexes confirm
            detail.status = "verified"
            # Use the first verified result for matched fields
            _, data = verified_results[0]
            detail.matched_title = data.get("matched_title")
            detail.matched_authors = data.get("matched_authors") or []
            detail.matched_year = data.get("matched_year")
            detail.matched_venue = data.get("matched_venue")
            detail.scores.update({idx: 1.0 for idx, _, _ in results})
            return detail

        if len(verified_results) == 1:
            idx, data = verified_results[0]
            detail.status = "verified"
            detail.matched_title = data.get("matched_title")
            detail.matched_authors = data.get("matched_authors") or []
            detail.matched_year = data.get("matched_year")
            detail.matched_venue = data.get("matched_venue")
            detail.scores[idx] = 1.0
            return detail

        # Check for probable matches
        probable_results = [(idx, data) for idx, status, data in results if status == "probable"]
        if probable_results:
            idx, data = probable_results[0]
            detail.status = "probable"
            detail.matched_title = data.get("matched_title")
            detail.matched_authors = data.get("matched_authors") or []
            detail.matched_year = data.get("matched_year")
            detail.matched_venue = data.get("matched_venue")
            return detail

        # All returned unresolved
        detail.status = "unresolved"
        detail.failure_reason = results[0][2].get("failure_reason") if results else "no_results"
        return detail

    def _detect_metadata_conflicts(
        self,
        ref_title: str,
        ref_authors: list[str],
        ref_year: Any,
        ref_venue: Any,
        matched_title: str | None,
        matched_authors: list[str],
        matched_year: int | None,
        matched_venue: str | None,
    ) -> list[str]:
        """Compare reference metadata with index-verified metadata."""
        conflicts: list[str] = []

        if matched_title and ref_title:
            sim = _title_similarity(ref_title, matched_title)
            if sim < 0.5:
                conflicts.append("title_mismatch")

        if ref_authors and matched_authors:
            if not _authors_match(ref_authors, matched_authors):
                conflicts.append("author_mismatch")

        if ref_year is not None and matched_year is not None:
            try:
                ref_y = int(ref_year)
                if abs(ref_y - matched_year) > 2:
                    conflicts.append("year_mismatch")
            except (TypeError, ValueError):
                pass

        if ref_venue and matched_venue:
            if str(ref_venue).strip().lower() != str(matched_venue).strip().lower():
                conflicts.append("venue_mismatch")

        return conflicts

    def _available_indexes(self) -> list[str]:
        """Check which indexes are actually reachable (one quick probe each)."""
        return self.allowed_indexes  # probe at first real use; list all configured


# ── Provenance extraction ─────────────────────────────────────────────────

def extract_academic_references(provenance_bundle: dict[str, Any]) -> list[dict[str, Any]]:
    """Find SourceDocuments in provenance that look like academic references."""
    refs: list[dict[str, Any]] = []
    docs = provenance_bundle.get("source_documents") or []

    for doc in docs:
        metadata = doc.get("metadata") or {}
        source_type = str(doc.get("source_type") or metadata.get("source_type") or "")
        external_ids = metadata.get("external_ids") or {}
        doi = external_ids.get("DOI") or metadata.get("doi") or ""
        arxiv_id = external_ids.get("ArXiv") or metadata.get("arxiv_id") or ""
        title = str(doc.get("title") or metadata.get("title") or "")
        canonical_uri = str(doc.get("canonical_uri") or "")
        authors = metadata.get("authors") or []

        # Determine if this looks like an academic reference
        is_academic = bool(
            doi
            or arxiv_id
            or source_type in {"arxiv_paper", "semantic_scholar_paper", "doi", "academic_paper"}
            or "arxiv.org" in canonical_uri
            or "doi.org" in canonical_uri
        )
        if not is_academic:
            continue

        refs.append({
            "document_id": doc.get("document_id"),
            "label": f"REF-{doc.get('document_id', '')[:8]}",
            "title": title,
            "authors": authors,
            "year": metadata.get("year"),
            "venue": metadata.get("venue"),
            "doi": doi,
            "arxiv_id": arxiv_id,
            "source_type": source_type,
            "canonical_uri": canonical_uri,
        })

    return refs


# ── Report rendering ──────────────────────────────────────────────────────

def render_reference_verification_section(report: ReferenceVerificationReport) -> list[str]:
    """Render the reference verification section as Markdown lines."""
    if report.total == 0:
        return []

    lines = [
        "## 12. 文献存在性校验",
        "",
        f"* 文献总数: {report.total}",
        f"* ✅ 已验证: {report.verified} ({report.verification_rate * 100:.1f}%)",
        f"* 🔶 可能存在: {report.probable} ({report.probable / max(report.total, 1) * 100:.1f}%)",
        f"* ❌ 元数据不一致: {report.inconsistent} ({report.inconsistent / max(report.total, 1) * 100:.1f}%)",
        f"* ⬜ 未解析: {report.unresolved} ({report.unresolved / max(report.total, 1) * 100:.1f}%)",
        f"* 网络失败: {report.network_failures}",
        "",
        f"> 通过 {', '.join(report.indexes_available)} 交叉验证文献存在性。",
        "> `verified`: ≥1 索引精确匹配；`probable`: 单一索引高相似度；`inconsistent`: 元数据冲突；`unresolved`: 无法解析。",
        "",
    ]

    # Gate results
    gate_alerts: list[str] = []
    if report.inconsistent > 0:
        gate_alerts.append(f"* ⚠️ {report.inconsistent} 条引用元数据不一致，已降级为「待核实」")
    if report.unresolved > 0:
        gate_alerts.append(f"* ⬜ {report.unresolved} 条引用无法验证，来源不可判定")
    if gate_alerts:
        lines += ["### 门禁结果", ""] + gate_alerts + [""]

    # Detail table
    lines += [
        "### 校验明细",
        "",
        "| 文献 | 状态 | 标识符 | 已查索引 | 匹配标题 | 说明 |",
        "|------|------|--------|----------|----------|------|",
    ]
    for d in report.details:
        icon = {"verified": "✅", "probable": "🔶", "inconsistent": "❌", "unresolved": "⬜"}.get(d.status, "⬜")
        identifier = d.identifier_value[:40] if d.identifier_value else "—"
        indexes = " ".join([f"{i}✓" if i in d.indexes_checked else "" for i in d.indexes_checked]).strip()
        matched = (d.matched_title or "—")[:60].replace("|", "\\|")
        note = ""
        if d.metadata_conflicts:
            note = "冲突: " + ", ".join(d.metadata_conflicts[:2])
        elif d.failure_reason:
            note = d.failure_reason
        elif d.status == "probable":
            note = f"相似度: {d.scores.get('title_similarity', '—')}"
        lines.append(
            f"| {d.ref_label} | {icon} {d.status} | {identifier} | {indexes} | {matched} | {note} |"
        )
    lines.append("")

    return lines
