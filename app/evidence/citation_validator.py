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

import json
import re
from dataclasses import dataclass, field
from typing import Any

from app.llm.base import LLMClient, LLMMessage

CITATION_PATTERN = re.compile(r"CIT-\d{3}-\d{2}")
SENTENCE_BOUNDARIES = ".!?。！？\n"


@dataclass
class CitationValidationDetail:
    citation_label: str
    verdict: str  # supported / weakly_supported / unsupported
    sentence: str  # the sentence containing this citation
    passage_text: str  # the referenced passage text
    keyword_overlap: float  # Jaccard similarity score
    judgment_source: str = "rule"


@dataclass
class CitationValidationReport:
    total: int = 0
    supported: int = 0
    weakly_supported: int = 0
    unsupported: int = 0
    details: list[CitationValidationDetail] = field(default_factory=list)
    llm_used: bool = False
    llm_provider: str | None = None
    llm_model: str | None = None
    token_in: int = 0
    token_out: int = 0

    @property
    def accuracy(self) -> float:
        if self.total == 0:
            return 1.0
        return round(self.supported / self.total, 4)

    @property
    def weak_rate(self) -> float:
        if self.total == 0:
            return 0.0
        return round(self.weakly_supported / self.total, 4)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "supported": self.supported,
            "weakly_supported": self.weakly_supported,
            "unsupported": self.unsupported,
            "accuracy": self.accuracy,
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


def _parse_llm_verdicts(content: str | None) -> dict[str, str]:
    if not content:
        return {}
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        return {}
    try:
        payload = json.loads(text[start : end + 1])
    except (json.JSONDecodeError, TypeError):
        return {}
    verdicts: dict[str, str] = {}
    for item in payload.get("verdicts") or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("citation_label") or "")
        verdict = str(item.get("verdict") or "")
        if label and verdict in {"supported", "weakly_supported", "unsupported"}:
            verdicts[label] = verdict
    return verdicts


def _apply_llm_secondary_judgment(
    report: CitationValidationReport,
    llm_client: LLMClient | None,
) -> CitationValidationReport:
    if llm_client is None or not llm_client.is_available() or not report.details:
        return report
    cases = [
        {
            "citation_label": detail.citation_label,
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
                "Return JSON only as {\"verdicts\":[{\"citation_label\":\"CIT-001-01\","
                "\"verdict\":\"supported|weakly_supported|unsupported\"}]}."
            ),
        ),
        LLMMessage(role="user", content=json.dumps(cases, ensure_ascii=False)),
    ]
    try:
        response = llm_client.complete(messages, temperature=0.0, max_tokens=1200)
    except Exception:
        return report
    if not response.success:
        return report
    verdicts = _parse_llm_verdicts(response.content)
    if not verdicts:
        return report
    for detail in report.details:
        verdict = verdicts.get(detail.citation_label)
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


def _find_citation_sentence(text: str, match_start: int) -> str:
    """Extract the sentence containing a citation match."""
    # Search backward for sentence boundary
    start = match_start
    while start > 0 and text[start - 1] not in SENTENCE_BOUNDARIES:
        start -= 1
    # Skip the boundary character
    if start > 0 and text[start - 1] in SENTENCE_BOUNDARIES:
        start = max(0, start)
    else:
        start = max(0, start)

    # Search forward for sentence boundary
    end = match_start
    while end < len(text) and text[end] not in SENTENCE_BOUNDARIES:
        end += 1
    # Include the boundary character
    if end < len(text) and text[end] in ".!?。！？":
        end += 1

    return text[start:end].strip()


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
    # Build citation_label → passage_text mapping
    passages = {
        str(item.get("passage_id")): item
        for item in provenance_bundle.get("passages") or []
    }
    citations = provenance_bundle.get("citations") or []

    label_to_passage: dict[str, str] = {}
    for cit in citations:
        label = str(cit.get("citation_label") or "")
        passage_id = str(cit.get("passage_id") or "")
        passage = passages.get(passage_id) or {}
        passage_text = str(passage.get("text") or "")
        if label and passage_text:
            label_to_passage[label] = passage_text

    # Find all CIT references in the report
    matches = []
    seen_labels: set[str] = set()
    for match in CITATION_PATTERN.finditer(report_text):
        label = match.group(0)
        if label in seen_labels:
            continue
        seen_labels.add(label)
        matches.append(match)
    if not matches:
        return CitationValidationReport()

    details: list[CitationValidationDetail] = []
    supported_count = 0
    weak_count = 0
    unsupported_count = 0

    for match in matches:
        label = match.group(0)
        passage_text = label_to_passage.get(label, "")

        if not passage_text:
            detail = CitationValidationDetail(
                citation_label=label,
                verdict="unsupported",
                sentence="",
                passage_text="",
                keyword_overlap=0.0,
            )
            details.append(detail)
            unsupported_count += 1
            continue

        sentence = _find_citation_sentence(report_text, match.start())
        sent_tokens = _tokenize(sentence)
        pass_tokens = _tokenize(passage_text)
        overlap = _jaccard_overlap(sent_tokens, pass_tokens)
        entity_count = _entity_co_occurrence(sentence, passage_text)

        if overlap >= min_supported_overlap and entity_count >= min_entity_co_occurrence:
            verdict = "supported"
            supported_count += 1
        elif overlap >= min_weak_overlap or entity_count >= 1:
            # Lenient: either some keyword overlap OR at least one shared entity
            verdict = "weakly_supported"
            weak_count += 1
        else:
            verdict = "unsupported"
            unsupported_count += 1

        details.append(CitationValidationDetail(
            citation_label=label,
            verdict=verdict,
            sentence=sentence[:300],
            passage_text=passage_text[:300],
            keyword_overlap=round(overlap, 4),
        ))

    report = CitationValidationReport(
        total=len(matches),
        supported=supported_count,
        weakly_supported=weak_count,
        unsupported=unsupported_count,
        details=details,
    )
    if use_llm:
        report = _apply_llm_secondary_judgment(report, llm_client)
    return report


def render_citation_validation_section(report: CitationValidationReport) -> list[str]:
    """Render the citation validation section as Markdown lines."""
    if report.total == 0:
        return []

    lines = [
        "## 11. 引用校验",
        "",
        f"* 引用总数: {report.total}",
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
