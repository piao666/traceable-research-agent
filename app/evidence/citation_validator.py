"""Citation accuracy validator — checks whether report citations are truly
supported by their referenced passage text.

Phase 7.5: Each CIT-XXX-XX reference in the report is checked against its
corresponding passage in the provenance bundle. The validator uses keyword
overlap (Jaccard) and entity co-occurrence to determine support level,
with an optional LLM secondary judgment.

Output: CitationValidationReport with supported / weakly_supported / unsupported
counts and accuracy metrics.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.budget import BudgetExceeded
from app.evidence.models import CitationOccurrence, ReportClaimOccurrence, ReportRevision
from app.llm.base import LLMClient, LLMMessage

CITATION_PATTERN = re.compile(r"CIT-\d{3}-\d{2}")
SENTENCE_BOUNDARIES = ".!?。！？\n"

_METADATA_ONLY_ROLES = {"official_metadata", "discovery_index"}
_BIBLIOGRAPHIC_TERMS = (
    "doi",
    "arxiv",
    "pmid",
    "author",
    "authored",
    "year",
    "publication",
    "published",
    "publisher",
    "journal",
    "venue",
    "title",
    "indexed",
    "exists",
    "existence",
    "文献存在",
    "作者",
    "年份",
    "发表",
    "出版",
    "期刊",
    "会议",
    "标题",
    "收录",
)
_RESEARCH_RESULT_TERMS = (
    "experiment",
    "experimental",
    "result",
    "performance",
    "accuracy",
    "benchmark",
    "conclusion",
    "demonstrate",
    "outperform",
    "improve",
    "metric",
    "实验",
    "结果",
    "性能",
    "准确率",
    "基准",
    "结论",
    "表明",
    "优于",
    "提升",
    "指标",
)


@dataclass
class CitationValidationDetail:
    citation_label: str
    verdict: str  # supported / weakly_supported / unsupported
    sentence: str  # the sentence containing this citation
    passage_text: str  # the referenced passage text
    keyword_overlap: float  # Jaccard similarity score
    judgment_source: str = "rule"
    evidence_role: str | None = None
    marker_start: int = 0
    marker_end: int = 0
    sentence_start: int = 0
    sentence_end: int = 0


@dataclass
class CitationValidationReport:
    occurrence_total: int = 0
    unique_citation_count: int = 0
    supported_occurrences: int = 0
    weakly_supported_occurrences: int = 0
    unsupported_occurrences: int = 0
    details: list[CitationValidationDetail] = field(default_factory=list)
    llm_used: bool = False
    llm_provider: str | None = None
    llm_model: str | None = None
    token_in: int = 0
    token_out: int = 0

    @property
    def total(self) -> int:
        return self.occurrence_total

    @property
    def supported(self) -> int:
        return self.supported_occurrences

    @supported.setter
    def supported(self, value: int) -> None:
        self.supported_occurrences = value

    @property
    def weakly_supported(self) -> int:
        return self.weakly_supported_occurrences

    @weakly_supported.setter
    def weakly_supported(self, value: int) -> None:
        self.weakly_supported_occurrences = value

    @property
    def unsupported(self) -> int:
        return self.unsupported_occurrences

    @unsupported.setter
    def unsupported(self, value: int) -> None:
        self.unsupported_occurrences = value

    @property
    def accuracy(self) -> float:
        if self.total == 0:
            return 0.0
        return round(self.supported / self.total, 4)

    @property
    def occurrence_accuracy(self) -> float:
        return self.accuracy

    @property
    def weak_rate(self) -> float:
        if self.total == 0:
            return 0.0
        return round(self.weakly_supported / self.total, 4)

    def to_dict(self) -> dict[str, Any]:
        return {
            "occurrence_total": self.occurrence_total,
            "unique_citation_count": self.unique_citation_count,
            "supported_occurrences": self.supported_occurrences,
            "weakly_supported_occurrences": self.weakly_supported_occurrences,
            "unsupported_occurrences": self.unsupported_occurrences,
            "occurrence_accuracy": self.occurrence_accuracy,
            "total": self.total,
            "supported": self.supported,
            "weakly_supported": self.weakly_supported,
            "unsupported": self.unsupported,
            "accuracy": self.accuracy,
            "evaluated": self.total > 0,
            "weak_rate": self.weak_rate,
            "llm_used": self.llm_used,
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model,
            "token_in": self.token_in,
            "token_out": self.token_out,
            "details": [
                {
                    "citation_label": d.citation_label,
                    "verdict": d.verdict,
                    "sentence": d.sentence[:300],
                    "passage_text": d.passage_text[:300],
                    "keyword_overlap": d.keyword_overlap,
                    "judgment_source": d.judgment_source,
                    "evidence_role": d.evidence_role,
                    "marker_start": d.marker_start,
                    "marker_end": d.marker_end,
                    "sentence_start": d.sentence_start,
                    "sentence_end": d.sentence_end,
                }
                for d in self.details
            ],
        }


def _tokenize(text: str) -> set[str]:
    """Extract lowercase alphanumeric tokens for overlap scoring."""
    lowered = text.lower()
    tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", lowered)
        if len(token) >= 2
    }
    for sequence in re.findall(r"[一-鿿]+", lowered):
        if len(sequence) == 1:
            continue
        tokens.update(sequence[index : index + 2] for index in range(len(sequence) - 1))
        if len(sequence) <= 8:
            tokens.add(sequence)
    return tokens


def _jaccard_overlap(a: set[str], b: set[str]) -> float:
    """Compute Jaccard similarity between two token sets."""
    if not a and not b:
        return 0.0
    if not a or not b:
        return 0.0
    intersection = a & b
    union = a | b
    return len(intersection) / len(union) if union else 0.0


def _entity_co_occurrence(sentence: str, passage: str) -> int:
    """Count how many capitalized/numeric entities co-occur in both texts."""
    sent_entities = set(re.findall(r"[A-Z][a-z]+(?:\s[A-Z][a-z]+)*|\d+\.?\d*", sentence))
    pass_entities = set(re.findall(r"[A-Z][a-z]+(?:\s[A-Z][a-z]+)*|\d+\.?\d*", passage))
    shared_cjk = _tokenize("".join(re.findall(r"[一-鿿]+", sentence))) & _tokenize(
        "".join(re.findall(r"[一-鿿]+", passage))
    )
    return len(sent_entities & pass_entities) + len(shared_cjk)


def _metadata_role_supports_claim(evidence_role: str, sentence: str) -> bool:
    """Limit metadata indexes to bibliographic claims defined by R12.1.6-B."""

    if evidence_role not in _METADATA_ONLY_ROLES:
        return True
    normalized = unicodedata.normalize("NFKC", sentence).casefold()
    if any(term in normalized for term in _RESEARCH_RESULT_TERMS):
        return False
    return any(term in normalized for term in _BIBLIOGRAPHIC_TERMS)


def _parse_llm_verdicts(
    content: str | None,
) -> tuple[dict[tuple[str, int], str], dict[str, str]]:
    if not content:
        return {}, {}
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        return {}, {}
    try:
        payload = json.loads(text[start : end + 1])
    except (json.JSONDecodeError, TypeError):
        return {}, {}
    occurrence_verdicts: dict[tuple[str, int], str] = {}
    legacy_verdicts: dict[str, str] = {}
    for item in payload.get("verdicts") or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("citation_label") or "")
        verdict = str(item.get("verdict") or "")
        if label and verdict in {"supported", "weakly_supported", "unsupported"}:
            marker_start = item.get("marker_start")
            if isinstance(marker_start, int) and not isinstance(marker_start, bool):
                occurrence_verdicts[(label, marker_start)] = verdict
            else:
                legacy_verdicts[label] = verdict
    return occurrence_verdicts, legacy_verdicts


def _apply_llm_secondary_judgment(
    report: CitationValidationReport,
    llm_client: LLMClient | None,
) -> CitationValidationReport:
    if llm_client is None or not llm_client.is_available() or not report.details:
        return report
    cases = [
        {
            "citation_label": detail.citation_label,
            "marker_start": detail.marker_start,
            "marker_end": detail.marker_end,
            "claim_sentence": detail.sentence,
            "passage": detail.passage_text,
            "rule_verdict": detail.verdict,
        }
        for detail in report.details
    ]
    messages = [
        LLMMessage(
            role="system",
            content=(
                "Judge whether each cited passage supports its claim sentence. "
                "Judge every marker independently and preserve marker_start. "
                "Return JSON only as {\"verdicts\":[{\"citation_label\":\"CIT-001-01\","
                "\"marker_start\":0,"
                "\"verdict\":\"supported|weakly_supported|unsupported\"}]}."
            ),
        ),
        LLMMessage(role="user", content=json.dumps(cases, ensure_ascii=False)),
    ]
    try:
        response = llm_client.complete(messages, temperature=0.0, max_tokens=1200)
    except BudgetExceeded:
        raise
    except Exception:
        return report
    if not response.success:
        return report
    occurrence_verdicts, legacy_verdicts = _parse_llm_verdicts(response.content)
    if not occurrence_verdicts and not legacy_verdicts:
        return report
    for detail in report.details:
        if detail.judgment_source == "evidence_role":
            continue
        verdict = occurrence_verdicts.get(
            (detail.citation_label, detail.marker_start)
        ) or legacy_verdicts.get(detail.citation_label)
        if verdict:
            detail.verdict = verdict
            detail.judgment_source = "llm"
    report.supported = sum(detail.verdict == "supported" for detail in report.details)
    report.weakly_supported = sum(
        detail.verdict == "weakly_supported" for detail in report.details
    )
    report.unsupported = sum(detail.verdict == "unsupported" for detail in report.details)
    report.llm_used = True
    report.llm_provider = response.provider
    report.llm_model = response.model
    if response.usage is not None:
        report.token_in = response.usage.prompt_tokens
        report.token_out = response.usage.completion_tokens
    return report


def _find_citation_sentence(text: str, match_start: int) -> tuple[str, int, int]:
    """Extract the sentence containing a citation match."""
    # Search backward for sentence boundary
    start = match_start
    def boundary(index: int) -> bool:
        char = text[index]
        if char == ".":
            # Decimal numbers, identifiers and URL host/path dots are not stops.
            return index + 1 == len(text) or text[index + 1].isspace()
        if char in "!?":
            return index + 1 == len(text) or text[index + 1].isspace()
        return char in "。！？\n"

    while start > 0 and not boundary(start - 1):
        start -= 1
    # A marker immediately following terminal punctuation still cites the
    # preceding claim: ``Claim. [CIT-001-01]``.
    if start > 0 and not text[start:match_start].strip(" \t\r\n[("):
        previous_start = start - 1
        while previous_start > 0 and not boundary(previous_start - 1):
            previous_start -= 1
        start = previous_start
    # Skip the boundary character
    if start > 0 and text[start - 1] in SENTENCE_BOUNDARIES:
        start = max(0, start)
    else:
        start = max(0, start)

    # Search forward for sentence boundary
    end = match_start
    while end < len(text) and not boundary(end):
        end += 1
    # Include the boundary character
    if end < len(text) and text[end] in ".!?。！？":
        end += 1

    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return text[start:end], start, end


def validate_citations(
    report_text: str,
    provenance_bundle: dict[str, Any],
    *,
    min_supported_overlap: float = 0.30,
    min_weak_overlap: float = 0.10,
    min_entity_co_occurrence: int = 1,
    llm_client: LLMClient | None = None,
    use_llm: bool = False,
) -> CitationValidationReport:
    """Validate all CIT references in a report against their passage text.

    For each CIT-XXX-XX found in the report:
    1. Locate the sentence containing it
    2. Look up the corresponding passage text in the provenance bundle
    3. Compute keyword overlap (Jaccard) + entity co-occurrence
    4. Classify as supported / weakly_supported / unsupported

    Args:
        report_text: The full Markdown report text.
        provenance_bundle: The provenance bundle dict from the evidence pipeline.
        min_supported_overlap: Jaccard threshold for "supported" (default 0.30).
        min_weak_overlap: Jaccard threshold for "weakly_supported" (default 0.10).
        min_entity_co_occurrence: Minimum co-occurring entities for "supported".

    Returns:
        CitationValidationReport with counts and per-citation details.
    """
    # Build citation_label → passage and evidence-role mappings.
    passages = {
        str(item.get("passage_id")): item
        for item in provenance_bundle.get("passages") or []
    }
    citations = provenance_bundle.get("citations") or []
    snapshots = {
        str(item.get("snapshot_id") or ""): item
        for item in provenance_bundle.get("source_snapshots") or []
    }
    documents = {
        str(item.get("document_id") or ""): item
        for item in provenance_bundle.get("source_documents") or []
    }

    label_to_passage: dict[str, str] = {}
    label_to_evidence_role: dict[str, str] = {}
    for cit in citations:
        label = str(cit.get("citation_label") or "")
        passage_id = str(cit.get("passage_id") or "")
        passage = passages.get(passage_id) or {}
        passage_text = str(passage.get("text") or "")
        if label and passage_text:
            label_to_passage[label] = passage_text
            passage_metadata = passage.get("metadata") or {}
            snapshot = snapshots.get(str(passage.get("snapshot_id") or "")) or {}
            document = documents.get(str(snapshot.get("document_id") or "")) or {}
            document_metadata = document.get("metadata") or {}
            role = str(
                (passage_metadata if isinstance(passage_metadata, dict) else {}).get("evidence_role")
                or (document_metadata if isinstance(document_metadata, dict) else {}).get("evidence_role")
                or "unknown"
            ).casefold()
            label_to_evidence_role[label] = role

    # Find all CIT references in the report
    matches = list(CITATION_PATTERN.finditer(report_text))
    if not matches:
        return CitationValidationReport()

    details: list[CitationValidationDetail] = []
    supported_count = 0
    weak_count = 0
    unsupported_count = 0

    for match in matches:
        label = match.group(0)
        passage_text = label_to_passage.get(label, "")
        evidence_role = label_to_evidence_role.get(label, "unknown")
        sentence, sentence_start, sentence_end = _find_citation_sentence(
            report_text, match.start()
        )

        if not passage_text:
            detail = CitationValidationDetail(
                citation_label=label,
                verdict="unsupported",
                sentence=sentence[:300],
                passage_text="",
                keyword_overlap=0.0,
                evidence_role=evidence_role,
                marker_start=match.start(),
                marker_end=match.end(),
                sentence_start=sentence_start,
                sentence_end=sentence_end,
            )
            details.append(detail)
            unsupported_count += 1
            continue

        sent_tokens = _tokenize(sentence)
        pass_tokens = _tokenize(passage_text)
        overlap = _jaccard_overlap(sent_tokens, pass_tokens)
        entity_count = _entity_co_occurrence(sentence, passage_text)

        if not _metadata_role_supports_claim(evidence_role, sentence):
            verdict = "unsupported"
            unsupported_count += 1
            judgment_source = "evidence_role"
        elif overlap >= min_supported_overlap and entity_count >= min_entity_co_occurrence:
            verdict = "supported"
            supported_count += 1
            judgment_source = "rule"
        elif overlap >= min_weak_overlap or entity_count >= 1:
            # Lenient: either some keyword overlap OR at least one shared entity
            verdict = "weakly_supported"
            weak_count += 1
            judgment_source = "rule"
        else:
            verdict = "unsupported"
            unsupported_count += 1
            judgment_source = "rule"

        details.append(CitationValidationDetail(
            citation_label=label,
            verdict=verdict,
            sentence=sentence[:300],
            passage_text=passage_text[:300],
            keyword_overlap=round(overlap, 4),
            judgment_source=judgment_source,
            evidence_role=evidence_role,
            marker_start=match.start(),
            marker_end=match.end(),
            sentence_start=sentence_start,
            sentence_end=sentence_end,
        ))

    report = CitationValidationReport(
        occurrence_total=len(matches),
        unique_citation_count=len({match.group(0) for match in matches}),
        supported_occurrences=supported_count,
        weakly_supported_occurrences=weak_count,
        unsupported_occurrences=unsupported_count,
        details=details,
    )
    if use_llm:
        report = _apply_llm_secondary_judgment(report, llm_client)
    return report


def validate_scope_citations(
    report_text: str,
    scope_bundle: dict[str, Any],
    **kwargs: Any,
) -> CitationValidationReport:
    """Validate Scope labels, including labels resolving to child passages."""

    return validate_citations(report_text, scope_bundle, **kwargs)


def extract_final_answer_section(markdown: str) -> str:
    """Extract only the rendered `## 3. 最终回答` body."""

    heading = re.search(r"(?m)^##\s+3\.\s*最终回答\s*$", markdown)
    if heading is None:
        return ""
    body_start = heading.end()
    next_heading = re.search(r"(?m)^##\s+", markdown[body_start:])
    body_end = body_start + next_heading.start() if next_heading else len(markdown)
    return markdown[body_start:body_end].strip()


def materialize_final_report_occurrences(
    db: Session,
    *,
    root_run_id: str,
    markdown: str,
    provenance_bundle: dict[str, Any],
    report_path: str | Path,
    scope_id: str | None = None,
    validation_report: CitationValidationReport | None = None,
) -> dict[str, Any]:
    """Persist one idempotent final-report revision and every citation marker."""

    final_answer = extract_final_answer_section(markdown)
    content_hash = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
    final_answer_hash = hashlib.sha256(final_answer.encode("utf-8")).hexdigest()
    report_revision_id = _stable_id("report_revision", root_run_id, content_hash)
    existing = db.get(ReportRevision, report_revision_id)
    if existing is not None and existing.status == "complete":
        return get_report_occurrence_bundle(db, report_revision_id)
    validation = validation_report or validate_scope_citations(
        final_answer,
        provenance_bundle,
        min_supported_overlap=0.15,
        min_weak_overlap=0.05,
    )
    if validation.occurrence_total != len(list(CITATION_PATTERN.finditer(final_answer))):
        validation = validate_scope_citations(
            final_answer,
            provenance_bundle,
            min_supported_overlap=0.15,
            min_weak_overlap=0.05,
        )

    citation_by_label = {
        str(item.get("citation_label") or ""): item
        for item in provenance_bundle.get("citations") or []
    }
    passage_by_id = {
        str(item.get("passage_id") or ""): item
        for item in provenance_bundle.get("passages") or []
    }
    revision = existing or ReportRevision(
        report_revision_id=report_revision_id,
        root_run_id=root_run_id,
        scope_id=scope_id,
        content_hash=content_hash,
        final_answer_hash=final_answer_hash,
        report_path=str(report_path),
        status="building",
    )
    try:
        revision.scope_id = scope_id
        revision.final_answer_hash = final_answer_hash
        revision.report_path = str(report_path)
        revision.status = "building"
        db.add(revision)
        db.flush()
        if existing is not None:
            _clear_report_occurrences(db, report_revision_id)

        details_by_sentence: dict[tuple[int, int], list[CitationValidationDetail]] = {}
        for detail in validation.details:
            details_by_sentence.setdefault(
                (detail.sentence_start, detail.sentence_end), []
            ).append(detail)
        for (sentence_start, sentence_end), details in sorted(details_by_sentence.items()):
            sentence = final_answer[sentence_start:sentence_end]
            claim_text = _claim_text(sentence)
            claim_occurrence_id = _stable_id(
                "report_claim_occurrence",
                report_revision_id,
                str(sentence_start),
                str(sentence_end),
            )
            db.add(
                ReportClaimOccurrence(
                    claim_occurrence_id=claim_occurrence_id,
                    report_revision_id=report_revision_id,
                    section="3. 最终回答",
                    claim_text=claim_text,
                    sentence_start=sentence_start,
                    sentence_end=sentence_end,
                    normalized_claim_text=_normalized_claim_text(claim_text),
                )
            )
            for detail in sorted(details, key=lambda item: item.marker_start):
                citation = citation_by_label.get(detail.citation_label) or {}
                requested_passage_id = str(citation.get("passage_id") or "")
                passage = passage_by_id.get(requested_passage_id) or {}
                passage_id = requested_passage_id if passage else None
                origin_run_id = (
                    str(
                        citation.get("origin_run_id")
                        or passage.get("origin_run_id")
                        or ""
                    )
                    or None
                )
                origin_trace_id = (
                    str(
                        citation.get("origin_trace_id")
                        or passage.get("origin_trace_id")
                        or passage.get("trace_id")
                        or ""
                    )
                    or None
                )
                db.add(
                    CitationOccurrence(
                        citation_occurrence_id=_stable_id(
                            "citation_occurrence",
                            claim_occurrence_id,
                            detail.citation_label,
                            str(detail.marker_start),
                            str(detail.marker_end),
                        ),
                        claim_occurrence_id=claim_occurrence_id,
                        citation_label=detail.citation_label,
                        passage_id=passage_id,
                        origin_run_id=origin_run_id,
                        origin_trace_id=origin_trace_id,
                        marker_start=detail.marker_start,
                        marker_end=detail.marker_end,
                        verdict=detail.verdict,
                        keyword_overlap=detail.keyword_overlap,
                        judgment_source=detail.judgment_source,
                    )
                )
        revision.status = "complete"
        db.commit()
    except Exception:
        db.rollback()
        raise
    return get_report_occurrence_bundle(db, report_revision_id)


def get_report_occurrence_bundle(
    db: Session,
    report_revision_id: str,
) -> dict[str, Any]:
    revision = db.get(ReportRevision, report_revision_id)
    if revision is None:
        raise ValueError("Report revision not found")
    claims = list(
        db.scalars(
            select(ReportClaimOccurrence)
            .where(ReportClaimOccurrence.report_revision_id == report_revision_id)
            .order_by(ReportClaimOccurrence.sentence_start)
        )
    )
    claim_ids = [claim.claim_occurrence_id for claim in claims]
    citations = (
        list(
            db.scalars(
                select(CitationOccurrence)
                .where(CitationOccurrence.claim_occurrence_id.in_(claim_ids))
                .order_by(CitationOccurrence.marker_start)
            )
        )
        if claim_ids
        else []
    )
    return {
        "report_revision": {
            "report_revision_id": revision.report_revision_id,
            "root_run_id": revision.root_run_id,
            "scope_id": revision.scope_id,
            "content_hash": revision.content_hash,
            "final_answer_hash": revision.final_answer_hash,
            "report_path": revision.report_path,
            "status": revision.status,
        },
        "claim_occurrences": [
            {
                "claim_occurrence_id": claim.claim_occurrence_id,
                "section": claim.section,
                "claim_text": claim.claim_text,
                "sentence_start": claim.sentence_start,
                "sentence_end": claim.sentence_end,
                "normalized_claim_text": claim.normalized_claim_text,
            }
            for claim in claims
        ],
        "citation_occurrences": [
            {
                "citation_occurrence_id": citation.citation_occurrence_id,
                "claim_occurrence_id": citation.claim_occurrence_id,
                "citation_label": citation.citation_label,
                "passage_id": citation.passage_id,
                "origin_run_id": citation.origin_run_id,
                "origin_trace_id": citation.origin_trace_id,
                "marker_start": citation.marker_start,
                "marker_end": citation.marker_end,
                "verdict": citation.verdict,
                "keyword_overlap": citation.keyword_overlap,
                "judgment_source": citation.judgment_source,
            }
            for citation in citations
        ],
    }


def _clear_report_occurrences(db: Session, report_revision_id: str) -> None:
    claims = list(
        db.scalars(
            select(ReportClaimOccurrence).where(
                ReportClaimOccurrence.report_revision_id == report_revision_id
            )
        )
    )
    claim_ids = [claim.claim_occurrence_id for claim in claims]
    if claim_ids:
        for citation in db.scalars(
            select(CitationOccurrence).where(
                CitationOccurrence.claim_occurrence_id.in_(claim_ids)
            )
        ):
            db.delete(citation)
    for claim in claims:
        db.delete(claim)
    db.flush()


def _claim_text(sentence: str) -> str:
    without_markers = CITATION_PATTERN.sub("", sentence)
    without_empty_brackets = re.sub(r"\[\s*\]", "", without_markers)
    compact = " ".join(without_empty_brackets.split()).strip()
    compact = re.sub(r"\s+([,.;:!?，。；：！？])", r"\1", compact)
    return re.sub(r"([.!?。！？])(?:\s*[.!?。！？])+$", r"\1", compact)


def _normalized_claim_text(claim_text: str) -> str:
    normalized = unicodedata.normalize("NFKC", claim_text).casefold()
    normalized = re.sub(r"[*_`#>]", " ", normalized)
    return " ".join(normalized.split())


def _stable_id(prefix: str, *parts: str) -> str:
    payload = "\x1f".join(parts).encode("utf-8")
    digest_length = max(8, 63 - len(prefix))
    return f"{prefix}_{hashlib.sha256(payload).hexdigest()[:digest_length]}"


def render_citation_validation_section(report: CitationValidationReport) -> list[str]:
    """Render the citation validation section as Markdown lines."""
    if report.total == 0:
        return ["## 11. 引用校验", "", "不可评估：最终回答中没有可校验的引用。", ""]

    lines = [
        "## 11. 引用校验",
        "",
        f"* 引用出现次数: {report.occurrence_total}",
        f"* 唯一引用编号数: {report.unique_citation_count}",
        f"* ✅ 充分支撑: {report.supported} ({report.accuracy * 100:.1f}%)",
        f"* ⚠️ 弱支撑: {report.weakly_supported} ({report.weak_rate * 100:.1f}%)",
        f"* ❌ 未支撑: {report.unsupported} ({report.unsupported / max(report.total, 1) * 100:.1f}%)",
        "",
        "> 引用准确性由关键词重叠率（Jaccard）+ 实体共现判定。",
        "> `supported`：重叠率 ≥ 30% 且至少 1 个共现实体；`weakly_supported`：重叠率 ≥ 10%；`unsupported`：不满足以上条件。",
        "",
    ]
    if report.llm_used:
        lines.insert(
            9,
            f"> 已启用 LLM 二次判定：`{report.llm_provider}` / `{report.llm_model or 'default'}`。",
        )

    weak_or_bad = [d for d in report.details if d.verdict != "supported"]
    if weak_or_bad:
        lines.extend([
            "### 弱支撑/未支撑明细",
            "",
            "| 引用编号 | 判定 | 重叠率 | 所在句子 | 原文片段 |",
            "|----------|------|--------|----------|----------|",
        ])
        for d in weak_or_bad:
            icon = "⚠️" if d.verdict == "weakly_supported" else "❌"
            sentence_escaped = d.sentence[:100].replace("\n", " ").replace("|", "\\|")
            passage_escaped = d.passage_text[:100].replace("\n", " ").replace("|", "\\|")
            lines.append(
                f"| [{d.citation_label}] | {icon} {d.verdict} | {d.keyword_overlap:.2%} "
                f"| {sentence_escaped} | {passage_escaped} |"
            )
        lines.append("")

    return lines
