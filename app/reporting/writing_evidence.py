"""A narrow, immutable projection of provenance for report writing.

This is intentionally not a retrieval abstraction.  It only decides which
already persisted passages may be shown to a report writer in one frozen
attempt.  Discovery records remain available for an index, never as factual
prompt material.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from app.evidence.policy import evidence_role_supports_claim
from app.evidence.qualification import CONTENT_BEARING_BASES
from app.agent.budget import estimate_text_tokens


_CONTENT_BEARING = CONTENT_BEARING_BASES
_DISCOVERY_ROLES = {"discovery", "discovery_index", "metadata", "search"}


@dataclass(frozen=True)
class WritingEvidenceUnit:
    citation_id: str
    passage_id: str
    snapshot_id: str | None
    source_url: str | None
    evidence_role: str
    content_basis: str
    text: str
    text_sha256: str
    passage_sha256: str
    snapshot_sha256: str | None
    locator: dict[str, Any] = field(default_factory=dict)
    origin_run_id: str | None = None
    origin_trace_id: str | None = None

    def to_prompt_dict(self) -> dict[str, Any]:
        # The writer needs source text and its allowed marker, not the audit
        # hashes/offsets repeated for every window. Keep those immutable on
        # the unit itself for citation validation and the writing manifest.
        return {
            "citation_id": self.citation_id,
            "source_url": self.source_url,
            "evidence_role": self.evidence_role,
            "content_basis": self.content_basis,
            "text": self.text,
        }


@dataclass(frozen=True)
class WritingEvidenceSet:
    factual_units: tuple[WritingEvidenceUnit, ...] = ()
    discovery_references: tuple[dict[str, str], ...] = ()
    allowed_citation_ids: tuple[str, ...] = ()
    gaps: tuple[dict[str, Any], ...] = ()
    version: str = "writing-evidence-v1"

    def prompt_payload(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "allowed_citation_ids": list(self.allowed_citation_ids),
            "factual_units": [unit.to_prompt_dict() for unit in self.factual_units],
            "gaps": list(self.gaps),
        }


def build_writing_evidence(
    bundle: dict[str, Any] | None,
    contract: dict[str, Any] | None = None,
    budget: int | None = None,
    *,
    focus_by_citation: dict[str, str] | None = None,
) -> WritingEvidenceSet:
    """Freeze eligible persisted passages without changing evidence identity.

    ``budget`` bounds only the projected text; it never causes a discovery
    snippet to become a factual unit.  Older bundles lacking a role retain a
    conservative compatibility path only for content-bearing passages; the
    validator still applies its normal role policy to actual claims.
    """
    source = bundle or {}
    passages = {str(item.get("passage_id") or ""): item for item in source.get("passages") or []}
    snapshots = {str(item.get("snapshot_id") or ""): item for item in source.get("source_snapshots") or []}
    documents = {str(item.get("document_id") or ""): item for item in source.get("source_documents") or []}
    token_limit = max(0, int(budget)) if budget is not None else None
    # Pre-role bundles are read-only legacy artifacts.  Keep their full-text
    # projection available for compatibility diagnostics, but do not mistake a
    # missing role in a modern bundle for an approved role.
    legacy_without_roles = not any(
        isinstance(item.get("metadata"), dict) and "evidence_role" in item["metadata"]
        for key in ("passages", "source_snapshots", "source_documents")
        for item in source.get(key) or []
    )
    used_tokens = estimate_text_tokens(json.dumps(WritingEvidenceSet().prompt_payload()))
    current_docs_required = (contract or {}).get("source_constraints", {}).get("current_official_documentation") is True
    candidates: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any], str, str, str, str | None]] = []
    discovery: list[dict[str, str]] = []

    for citation in source.get("citations") or []:
        label = str(citation.get("citation_label") or "")
        passage = passages.get(str(citation.get("passage_id") or "")) or {}
        if not label or not passage:
            continue
        snapshot = snapshots.get(str(passage.get("snapshot_id") or "")) or {}
        document = documents.get(str(snapshot.get("document_id") or "")) or {}
        pmeta = passage.get("metadata") if isinstance(passage.get("metadata"), dict) else {}
        smeta = snapshot.get("metadata") if isinstance(snapshot.get("metadata"), dict) else {}
        dmeta = document.get("metadata") if isinstance(document.get("metadata"), dict) else {}
        # A document can own multiple retrieval snapshots for one URL.  Use
        # the snapshot's immutable acquisition role before a legacy document
        # role, so a discovery snapshot cannot be promoted by a later fetch.
        role = str(smeta.get("evidence_role") or dmeta.get("evidence_role") or pmeta.get("evidence_role") or "unknown").casefold()
        basis = str(passage.get("content_basis") or pmeta.get("content_basis") or "snippet_only").casefold()
        url = str(document.get("canonical_uri") or snapshot.get("source_url") or "") or None
        text = str(passage.get("text") or "")
        if role in _DISCOVERY_ROLES or basis not in _CONTENT_BEARING:
            if url:
                discovery.append({"citation_id": label, "source_url": url})
            continue
        if current_docs_required and (
            dmeta.get("source_class") != "official"
            or smeta.get("current_channel_verified") is not True
        ):
            continue
        # An explicitly supplied role must be capable of supporting a factual
        # claim. Missing role is retained for legacy bundle projection only;
        # validation remains fail-closed when the claim is checked.
        if role == "unknown" and not legacy_without_roles:
            continue
        if role != "unknown" and not evidence_role_supports_claim(role, "A substantive factual finding."):
            continue
        passage_hash = str(passage.get("content_hash") or "")
        actual_passage_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        # Never use a projection whose persisted passage bytes do not match.
        if passage_hash and passage_hash != actual_passage_hash:
            continue
        candidates.append((citation, passage, snapshot, role, basis, text, url))

    # Select complete, relevant paragraph windows round-robin.  This avoids
    # silently privileging page headers and preserves coverage across nodes.
    factual: list[WritingEvidenceUnit] = []
    terms = _contract_terms(contract)
    pending = list(candidates)
    while pending:
        citation, passage, snapshot, role, basis, text, url = pending.pop(0)
        # Feedback only ranks slices of an already admitted immutable passage.
        # It never supplies evidence text or makes a citation supported.
        focus = (focus_by_citation or {}).get(str(citation.get("citation_label")))
        window_terms = _lexical_tokens(focus[:2000]) if isinstance(focus, str) and focus.strip() else terms
        remaining = None if token_limit is None else token_limit - used_tokens
        if remaining is not None and remaining <= 0:
            break
        # Keep an offset into the immutable persisted passage.  ``split`` and
        # ``strip`` lose that information and make a repeated paragraph
        # impossible to bind unambiguously during validation.
        windows = [
            # Group 1 is the paragraph, while group 0 can include the leading
            # separator.  Persisting group-0 offsets made every later
            # paragraph point two bytes before its actual immutable source.
            (match.start(1), match.end(1), match.group(1))
            for match in re.finditer(r"(?s)(?:^|\n\n)(.*?)(?=\n\n|\Z)", text)
            if match.group(1).strip()
        ]
        paragraph_windows = [
            (start + len(value) - len(value.lstrip()), start + len(value.rstrip()), value.strip())
            for start, _end, value in windows
        ]
        # Raw HTML fallbacks frequently preserve no paragraph separators.  In
        # that case the old projection handed the writer an entire noisy page,
        # which diluted later claim-to-window validation.  Sentence windows
        # remain exact slices of the immutable parent, not newly inferred
        # evidence, and are considered only when a task term actually matches.
        sentence_windows = (
            _sentence_windows(text)
            if len(paragraph_windows) == 1 and len(text) > 600 else []
        )
        candidates = paragraph_windows + sentence_windows
        overhead = estimate_text_tokens(json.dumps({
            "citation_id": citation.get("citation_label"), "source_url": url,
            "evidence_role": role, "content_basis": basis, "text": "",
        }, ensure_ascii=False)) + 24
        eligible = [item for item in candidates if remaining is None or estimate_text_tokens(item[2]) + overhead <= remaining]
        relevant = [
            item for item in eligible
            if window_terms and _relevance(item[2], window_terms)[0] > 0
        ]
        if relevant:
            # A focused, matching sentence is more useful than a page-sized
            # parent with the same one matching term.  Ties prefer the smaller
            # exact window; the source hashes and offsets are validated later.
            chosen = max(
                relevant,
                key=lambda item: (_relevance(item[2], window_terms)[0], -len(item[2])),
            )
        else:
            chosen = max(eligible, key=lambda item: _relevance(item[2], window_terms), default=None)
        projected = chosen[2] if chosen else ""
        if not projected and (remaining is None or estimate_text_tokens(text) + overhead <= remaining):
            projected = text
        if not projected:
            continue
        actual_passage_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        factual.append(WritingEvidenceUnit(
            citation_id=str(citation.get("citation_label")), passage_id=str(passage.get("passage_id")),
            snapshot_id=str(passage.get("snapshot_id") or "") or None,
            source_url=url, evidence_role=role, content_basis=basis, text=projected,
            text_sha256=hashlib.sha256(projected.encode("utf-8")).hexdigest(),
            passage_sha256=actual_passage_hash,
            snapshot_sha256=str(snapshot.get("content_hash") or "") or None,
            locator={
                **dict(passage.get("locator") or {}),
                "writing_window_start": chosen[0] if chosen else 0,
                "writing_window_end": chosen[1] if chosen else len(projected),
            },
            origin_run_id=str(citation.get("origin_run_id") or passage.get("origin_run_id") or "") or None,
            origin_trace_id=str(citation.get("origin_trace_id") or passage.get("origin_trace_id") or passage.get("trace_id") or "") or None,
        ))
        used_tokens += estimate_text_tokens(projected) + overhead
    gaps: list[dict[str, Any]] = []
    if not factual:
        gaps.append({"code": "no_eligible_writing_evidence", "requirement_id": "substantive_evidence", "retryable": True, "detail": "No persisted content-bearing passage is eligible for factual writing."})
    return WritingEvidenceSet(tuple(factual), tuple(discovery), tuple(unit.citation_id for unit in factual), tuple(gaps))


def _contract_terms(contract: dict[str, Any] | None) -> set[str]:
    """Extract task terms from both legacy and current task contracts.

    ``original_task`` is the canonical field produced by the planner.  The
    former projection ignored it, so a normal contract had no query terms and
    selected the first paragraph even when the relevant evidence was later in
    the passage.
    """
    values: list[str] = []
    wanted = {"topic", "question", "original_task", "task", "goal", "required_subquestions", "requirements"}

    def collect(value: Any, key: str | None = None) -> None:
        if isinstance(value, dict):
            for child_key, child in value.items():
                if child_key in wanted or key in {"required_subquestions", "requirements"}:
                    collect(child, child_key)
            return
        if isinstance(value, (list, tuple)):
            for child in value:
                collect(child, key)
            return
        if key in wanted and value:
            values.append(str(value))

    for key, value in (contract or {}).items():
        collect(value, key)
    return _lexical_tokens(" ".join(values))


def _lexical_tokens(text: str) -> set[str]:
    """Tokenize Latin words plus CJK bigrams for compact relevance matching."""
    tokens = {
        token.casefold()
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", text)
    }
    for sequence in re.findall(r"[\u3400-\u9fff]+", text):
        if len(sequence) >= 2:
            tokens.update(sequence[index:index + 2] for index in range(len(sequence) - 1))
            if len(sequence) <= 8:
                tokens.add(sequence)
    return tokens


def _sentence_windows(text: str) -> list[tuple[int, int, str]]:
    """Return whitespace-trimmed, exact parent slices at sentence boundaries."""
    windows: list[tuple[int, int, str]] = []
    for match in re.finditer(r"[^.!?。！？\r\n]+(?:[.!?。！？]+|(?=\r?\n)|$)", text):
        value = match.group(0)
        stripped = value.strip()
        if not stripped:
            continue
        start = match.start() + len(value) - len(value.lstrip())
        end = match.start() + len(value.rstrip())
        windows.append((start, end, stripped))
    return windows


def _relevance(text: str, terms: set[str]) -> tuple[int, int]:
    # Whitespace splitting makes every Chinese paragraph score zero.  Keep the
    # same compact local heuristic, but tokenize CJK runs as well as English
    # words so the default Chinese report path does not always select page
    # headers over later task-relevant passages.
    tokens = _lexical_tokens(text)
    return (len(tokens & terms), min(len(text), 2000))
