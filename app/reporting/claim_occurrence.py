"""Deterministic final-answer claim segmentation for report integrity."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


CITATION_PATTERN = re.compile(r"CIT-\d{3}-\d{2}")
_FENCE_PATTERN = re.compile(r"^\s*(?:```|~~~)")
_HEADING_PATTERN = re.compile(r"^\s{0,3}#{1,6}\s+")
_LIST_PREFIX_PATTERN = re.compile(r"^\s*(?:[-+*]|\d+[.)、])\s+")
_QUOTE_PREFIX_PATTERN = re.compile(r"^\s*>\s?")
_PURE_URL_PATTERN = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_PURE_MARKDOWN_LINK_PATTERN = re.compile(
    r"!?\[[^\]]*\]\((?:https?://|www\.)[^)]+\)", re.IGNORECASE
)
_MARKDOWN_LINK_PATTERN = re.compile(r"!?\[([^\]]*)\]\([^)]+\)")
_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class FinalClaimSpan:
    sentence_start: int
    sentence_end: int
    raw_text: str
    claim_text: str
    normalized_claim_text: str
    has_citation_marker: bool
    citation_labels: tuple[str, ...]
    is_claim_candidate: bool


def segment_final_answer_claims(final_answer: str) -> list[FinalClaimSpan]:
    """Segment only final-answer text without an LLM or semantic ontology."""

    text = str(final_answer or "")
    spans: list[FinalClaimSpan] = []
    offset = 0
    in_code_fence = False
    for line in text.splitlines(keepends=True):
        line_body = line.rstrip("\r\n")
        stripped = line_body.strip()
        if _FENCE_PATTERN.match(line_body):
            in_code_fence = not in_code_fence
            offset += len(line)
            continue
        if in_code_fence or not stripped:
            offset += len(line)
            continue

        content_start = offset + len(line_body) - len(line_body.lstrip())
        content_end = offset + len(line_body)
        is_heading = bool(_HEADING_PATTERN.match(line_body))
        prefix = _LIST_PREFIX_PATTERN.match(line_body)
        if prefix:
            content_start = offset + prefix.end()
        else:
            quote_prefix = _QUOTE_PREFIX_PATTERN.match(line_body)
            if quote_prefix:
                content_start = offset + quote_prefix.end()
        for sentence_start, sentence_end in _sentence_ranges(
            text, content_start, content_end
        ):
            raw_text = text[sentence_start:sentence_end]
            claim_text = clean_claim_text(raw_text)
            labels = tuple(CITATION_PATTERN.findall(raw_text))
            spans.append(
                FinalClaimSpan(
                    sentence_start=sentence_start,
                    sentence_end=sentence_end,
                    raw_text=raw_text,
                    claim_text=claim_text,
                    normalized_claim_text=normalize_claim_text(claim_text),
                    has_citation_marker=bool(labels),
                    citation_labels=labels,
                    is_claim_candidate=(
                        not is_heading and _is_claim_candidate(raw_text, claim_text)
                    ),
                )
            )
        offset += len(line)

    # ``splitlines(keepends=True)`` omits no content, including a final line
    # without a newline. An empty input correctly produces an empty universe.
    return spans


def clean_claim_text(raw_text: str) -> str:
    """Remove citation and Markdown presentation syntax from a claim."""

    text = CITATION_PATTERN.sub("", str(raw_text or ""))
    text = re.sub(r"\[\s*\]", "", text)
    text = _MARKDOWN_LINK_PATTERN.sub(lambda match: match.group(1), text)
    text = _HTML_TAG_PATTERN.sub(" ", text)
    text = re.sub(r"^[ \t]*(?:[-+*]|\d+[.)、])\s+", "", text)
    text = re.sub(r"^[ \t]*>\s?", "", text)
    text = re.sub(r"[*_`#~]", "", text)
    text = " ".join(text.split()).strip()
    text = re.sub(r"\s+([,.;:!?，。；：！？])", r"\1", text)
    return re.sub(r"([.!?。！？])(?:\s*[.!?。！？])+$", r"\1", text)


def normalize_claim_text(claim_text: str) -> str:
    """Normalize content only; citation and Markdown syntax is already absent."""

    normalized = unicodedata.normalize("NFKC", str(claim_text or "")).casefold()
    return " ".join(normalized.split())


def claim_span_for_offset(
    spans: list[FinalClaimSpan],
    offset: int,
) -> FinalClaimSpan | None:
    """Return the claim candidate containing a citation marker offset."""

    return next(
        (
            span
            for span in spans
            if span.is_claim_candidate
            and span.sentence_start <= offset < span.sentence_end
        ),
        None,
    )


def _sentence_ranges(text: str, start: int, end: int):
    cursor = start
    while cursor < end:
        while cursor < end and text[cursor].isspace():
            cursor += 1
        if cursor >= end:
            break
        sentence_start = cursor
        while cursor < end:
            if _is_sentence_terminal(text, cursor, end):
                cursor += 1
                sentence_end = _include_trailing_citations(text, cursor, end)
                yield sentence_start, sentence_end
                cursor = sentence_end
                break
            cursor += 1
        else:
            yield sentence_start, end


def _is_sentence_terminal(text: str, index: int, end: int) -> bool:
    char = text[index]
    if char in "。！？":
        return True
    if char not in ".!?":
        return False
    return index + 1 >= end or text[index + 1].isspace()


def _include_trailing_citations(text: str, cursor: int, end: int) -> int:
    scan = cursor
    while scan < end:
        whitespace_end = scan
        while whitespace_end < end and text[whitespace_end] in " \t":
            whitespace_end += 1
        marker = re.match(r"\[\s*CIT-\d{3}-\d{2}\s*\]", text[whitespace_end:end])
        if marker is None:
            break
        scan = whitespace_end + marker.end()
    return scan


def _is_claim_candidate(raw_text: str, claim_text: str) -> bool:
    raw = str(raw_text or "").strip()
    claim = str(claim_text or "").strip()
    if not claim:
        return False
    without_prefix = _LIST_PREFIX_PATTERN.sub("", raw)
    if _PURE_URL_PATTERN.fullmatch(without_prefix):
        return False
    if _PURE_MARKDOWN_LINK_PATTERN.fullmatch(without_prefix):
        return False
    if not re.search(r"[A-Za-z\u3400-\u9fff]", claim):
        return False
    return True


__all__ = [
    "CITATION_PATTERN",
    "FinalClaimSpan",
    "claim_span_for_offset",
    "clean_claim_text",
    "normalize_claim_text",
    "segment_final_answer_claims",
]
