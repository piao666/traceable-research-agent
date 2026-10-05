"""Pure contract gate for task-relevant, content-bearing web evidence."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any


from app.evidence.qualification import CONTENT_BEARING_BASES

_CONTENT_BASES = CONTENT_BEARING_BASES
_INELIGIBLE_ROLES = {"", "unknown", "discovery", "discovery_index", "search_result", "metadata"}
# These words describe the question the researcher will answer, not a phrase
# that must occur verbatim in a source.  A source can explain an Agent routing
# mechanism without literally calling it an "impact" or "implication".
_ANALYTIC_INTENT_TERMS = {"影响", "impact", "effect", "implication"}
# Lexical equivalents for common research concepts.  Each original term is
# still one concept: "evaluation" and "benchmark" must not count twice.
# This is a relevance hint, never a replacement for body provenance or
# citation-level support.
_CONCEPT_EQUIVALENTS = {
    "评测": ("evaluation", "benchmark", "assessment"),
    "评估": ("evaluation", "assessment"),
    "基准": ("benchmark",),
    "框架": ("framework",),
    "方法": ("method", "methodology"),
    "指标": ("metric", "measure"),
    "影响": ("impact", "effect", "implication"),
    "模型": ("model",),
    "智能": ("agent", "agentic"),
    "代理": ("agent", "agentic"),
    "证据": ("evidence",),
    "来源": ("source",),
}
_STOP_TERMS = {
    "search", "fetch", "answer", "explain", "research", "official", "documentation", "source", "sources", "page", "with", "from", "about", "that", "this", "what", "which", "when", "where", "their", "they", "the", "and", "for", "are", "please",
    "请", "搜索", "抓取", "回答", "调研", "官方", "页面", "文档", "来源", "证据", "什么", "如何", "以及", "相关", "说明", "解释", "比较", "分析", "研究", "一个", "这个", "那个",
}


@dataclass(frozen=True)
class EvidenceGap:
    code: str
    requirement_id: str
    source_id: str | None = None
    passage_id: str | None = None
    retryable: bool = True
    detail: str = ""

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class EvidenceAssessment:
    passed: bool
    gaps: tuple[EvidenceGap, ...]
    eligible_passage_ids: tuple[str, ...]
    counts: dict[str, int]

    def as_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "gaps": [gap.as_dict() for gap in self.gaps],
            "eligible_passage_ids": list(self.eligible_passage_ids),
            "counts": dict(self.counts),
        }


def assess_required_evidence(
    contract: dict[str, Any] | None,
    provenance_bundle: dict[str, Any] | None,
) -> EvidenceAssessment:
    """Assess only whether required evidence exists, never claim truth.

    This deliberately accepts an existing partial read: it is content-bearing
    provenance, unlike a discovery snippet. Claim-level support remains the
    later citation-validation responsibility.
    """
    contract = contract if isinstance(contract, dict) else {}
    bundle = provenance_bundle if isinstance(provenance_bundle, dict) else {}
    requirement = str(contract.get("evidence_requirement") or "unspecified").casefold()
    required_bases = {str(item).casefold() for item in contract.get("required_content_basis") or []}
    substantive = requirement == "substantive" or bool(required_bases)
    passages = [item for item in bundle.get("passages") or [] if isinstance(item, dict)]
    requirements = _requirements(contract, substantive)
    sources = _source_context(bundle)
    eligible: list[str] = []
    rejected_snippets = 0
    rejected_roles = 0
    rejected_relevance = 0
    rejected_quality = 0
    rejected_source_constraint = 0
    rejected_current_channel = 0
    rejected_provenance = 0
    rejected_demonstration = 0
    covered: dict[str, list[str]] = {item["requirement_id"]: [] for item in requirements}
    for passage in passages:
        passage_id = str(passage.get("passage_id") or "")
        basis = str(passage.get("content_basis") or "").casefold()
        metadata = passage.get("metadata") if isinstance(passage.get("metadata"), dict) else {}
        source = sources.get(str(passage.get("snapshot_id") or ""), {})
        # A document identifies a source, but it may have both a discovery
        # snapshot and a later full-text snapshot.  The snapshot records how
        # these exact bytes were obtained, so it is authoritative for role;
        # document role is legacy fallback only.  This prevents a search
        # snippet from being retroactively promoted when the same URL is read.
        role = str(source.get("snapshot_evidence_role") or source.get("document_evidence_role") or passage.get("evidence_role") or metadata.get("evidence_role") or "unknown").casefold()
        # If a bundle carries source/snapshot records, a passage must resolve
        # through that persisted lineage.  Do not accept a model-shaped
        # source_id or URL as a substitute for the actual snapshot relation.
        if substantive and not source:
            rejected_provenance += 1
            continue
        source_metadata = source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
        if any(
            value is True
            for value in (
                metadata.get("is_mock"), metadata.get("is_fallback"),
                source.get("is_mock"), source.get("is_fallback"),
                source_metadata.get("is_mock"), source_metadata.get("is_fallback"),
            )
        ):
            rejected_demonstration += 1
            continue
        if basis not in _CONTENT_BASES or (required_bases and basis not in required_bases):
            rejected_snippets += 1
            continue
        if role in _INELIGIBLE_ROLES:
            rejected_roles += 1
            continue
        quality = metadata.get("quality") or source.get("quality") or {}
        if isinstance(quality, dict) and quality.get("usable") is False:
            rejected_quality += 1
            continue
        constraints = contract.get("source_constraints") or {}
        if bool(constraints.get("official_only")) and source.get("official") is not True:
            rejected_source_constraint += 1
            continue
        if constraints.get("current_official_documentation") is True and source.get("current_channel_verified") is not True:
            rejected_current_channel += 1
            continue
        matches = [requirement for requirement in requirements if _passage_matches_requirement(passage, source, requirement)]
        if substantive and not matches:
            rejected_relevance += 1
            continue
        if passage_id:
            eligible.append(passage_id)
            for requirement in matches:
                covered[requirement["requirement_id"]].append(passage_id)
    gaps: list[EvidenceGap] = []
    if substantive and not eligible:
        code = "required_evidence_missing"
        detail = "No task-eligible content-bearing passage was materialized."
        if passages and rejected_snippets == len(passages):
            code, detail = "discovery_not_full_text", "Discovery snippets cannot satisfy substantive evidence requirements."
        elif passages and rejected_roles:
            code, detail = "ineligible_evidence_role", "Content was not classified as independently usable evidence."
        elif passages and rejected_provenance:
            code, detail = "evidence_provenance_unresolved", "Passages did not resolve to persisted source snapshots."
        elif passages and rejected_demonstration:
            code, detail = "demonstration_evidence_rejected", "Mock or fallback material cannot satisfy real substantive evidence requirements."
        elif passages and rejected_quality:
            code, detail = "content_quality_unusable", "Fetched content was classified as a page shell, login wall, or otherwise unusable page."
        elif passages and rejected_source_constraint:
            code, detail = "source_constraint_unmet", "No eligible passage satisfied the requested source constraint."
        elif passages and rejected_current_channel:
            code, detail = "current_source_channel_unmet", "No fetched official passage was verified on a configured current documentation channel."
        elif passages and rejected_relevance:
            code, detail = "task_relevant_evidence_missing", "Content-bearing passages did not match the task scope."
        gaps.append(EvidenceGap(code, "substantive_web_evidence", retryable=True, detail=detail))
    elif substantive:
        for requirement in requirements:
            requirement_id = requirement["requirement_id"]
            if not covered[requirement_id]:
                detail = ("A trace-backed file_reader body from workspace/docs is required; public web search cannot describe this project's evaluation."
                          if requirement.get("source_scope") == "local_project"
                          else "No eligible passage matched this required task scope.")
                gaps.append(EvidenceGap(
                    "required_evidence_coverage_incomplete", requirement_id,
                    retryable=True,
                    detail=detail,
                ))
    return EvidenceAssessment(
        passed=not gaps,
        gaps=tuple(gaps),
        eligible_passage_ids=tuple(dict.fromkeys(eligible)),
        counts={
            "passages": len(passages),
            "eligible_passages": len(eligible),
            "requirements": len(requirements),
            "covered_requirements": sum(bool(value) for value in covered.values()),
            "rejected_content_basis": rejected_snippets,
            "rejected_evidence_role": rejected_roles,
            "rejected_task_relevance": rejected_relevance,
            "rejected_content_quality": rejected_quality,
            "rejected_source_constraint": rejected_source_constraint,
            "rejected_current_channel": rejected_current_channel,
            "rejected_provenance": rejected_provenance,
            "rejected_demonstration": rejected_demonstration,
        },
    )


def _source_context(bundle: dict[str, Any]) -> dict[str, dict[str, Any]]:
    documents = {str(item.get("document_id") or ""): item for item in bundle.get("source_documents") or [] if isinstance(item, dict)}
    result: dict[str, dict[str, Any]] = {}
    for snapshot in bundle.get("source_snapshots") or []:
        if not isinstance(snapshot, dict):
            continue
        document = documents.get(str(snapshot.get("document_id") or ""), {})
        doc_metadata = document.get("metadata") if isinstance(document.get("metadata"), dict) else {}
        result[str(snapshot.get("snapshot_id") or "")] = {
            "title": document.get("title") or "",
            "url": document.get("canonical_uri") or "",
            "source_type": document.get("source_type") or "",
            "snapshot_evidence_role": (
                snapshot.get("metadata", {}).get("evidence_role")
                if isinstance(snapshot.get("metadata"), dict) else ""
            ) or "",
            "document_evidence_role": doc_metadata.get("evidence_role") or "",
            # Explicit metadata wins, while a persisted policy classification
            # is also authoritative for configured official/regulatory
            # domains. This keeps an official-only contract usable after a
            # normal fetch where the adapter did not redundantly emit an
            # ``official`` boolean; arbitrary primary-content labels do not
            # qualify.
            "official": (
                doc_metadata.get("official") is True
                or str(doc_metadata.get("source_class") or "").casefold()
                in {"official", "official_code", "regulatory"}
            ),
            "current_channel_verified": (
                snapshot.get("metadata", {}).get("current_channel_verified") is True
                if isinstance(snapshot.get("metadata"), dict) else False
            ),
            "source_identity": doc_metadata.get("source_identity"),
            "quality": snapshot.get("metadata", {}).get("quality") if isinstance(snapshot.get("metadata"), dict) else {},
            "is_mock": doc_metadata.get("is_mock") is True or (snapshot.get("metadata") or {}).get("is_mock") is True,
            "is_fallback": doc_metadata.get("is_fallback") is True or (snapshot.get("metadata") or {}).get("is_fallback") is True,
            "metadata": doc_metadata,
        }
    return result


def _requirements(contract: dict[str, Any], substantive: bool) -> list[dict[str, object]]:
    explicit = [item for item in (contract.get("evidence_scope_requirements") or contract.get("requirements") or [])
                if isinstance(item, dict) and item.get("mandatory", True)]
    if explicit:
        return [{"requirement_id": str(item.get("requirement_id") or f"requirement-{index}"),
                 "terms": _requirement_terms(item), "entity_terms": _task_terms(str(item.get("entity") or "")),
                 "dimension_terms": _task_terms(str(item.get("dimension") or "")),
                 "match_mode": item.get("match_mode"), "source_scope": item.get("source_scope")}
                for index, item in enumerate(explicit, 1)]
    return ([{"requirement_id": "substantive_web_evidence", "terms": _task_terms(str(contract.get("original_task") or ""))}] if substantive else [])


def _requirement_terms(requirement: dict[str, Any]) -> tuple[str, ...]:
    entity = str(requirement.get("entity") or "")
    dimension = str(requirement.get("dimension") or "")
    terms = _task_terms(f"{entity} {dimension}")
    return terms or _task_terms(str(requirement.get("question") or requirement.get("description") or ""))


def _passage_matches_requirement(passage: dict[str, Any], source: dict[str, Any], requirement: dict[str, object]) -> bool:
    """Conservative lexical scope check that refuses unrelated demo bodies.

    It is intentionally permissive for underspecified tasks; semantic and
    claim-level matching belong to later stages, not this P0 admission gate.
    """
    source_scope = requirement.get("source_scope")
    if source_scope == "external_web" and source.get("source_type") in {"file", "sql"}:
        return False
    if source_scope == "local_project":
        source_metadata = source.get("metadata") or {}
        allowed_root = str(source_metadata.get("file_allowed_root") or "").replace("\\", "/").rstrip("/").casefold()
        docs_root = str(source_metadata.get("file_docs_root") or "").replace("\\", "/").rstrip("/").casefold()
        if (source.get("source_type") != "file" or not allowed_root or allowed_root != docs_root
                or source_metadata.get("file_safe_path") is not True
                or source_metadata.get("file_approved_outside_allowed_roots") is True):
            return False
    terms = tuple(requirement.get("terms") or ())
    if not terms:
        return True
    metadata = passage.get("metadata") if isinstance(passage.get("metadata"), dict) else {}
    # For an explicit entity + dimension contract, the fetched body itself
    # must connect both concepts locally. A page title naming Muse and a
    # distant paragraph about Jev's impact is not Muse-impact evidence.
    if requirement.get("match_mode") == "all_components":
        body = str(passage.get("text") or "").casefold()
        entity_terms = tuple(requirement.get("entity_terms") or ())
        dimension_terms = tuple(
            term for term in (requirement.get("dimension_terms") or ())
            if term not in _ANALYTIC_INTENT_TERMS
        )
        if entity_terms and not dimension_terms:
            if not any(
                re.search(
                    rf"(?<![a-z0-9]){re.escape(entity)}(?![a-z0-9])" if entity.isascii() else re.escape(entity),
                    body,
                )
                for entity in entity_terms
            ):
                return False
        if entity_terms and dimension_terms:
            for entity in entity_terms:
                for match in re.finditer(
                    rf"(?<![a-z0-9]){re.escape(entity)}(?![a-z0-9])" if entity.isascii() else re.escape(entity),
                    body,
                ):
                    window = body[max(0, match.start() - 450):match.end() + 450]
                    if all(
                        any(candidate in window for candidate in (term, *_CONCEPT_EQUIVALENTS.get(term, ())))
                        for term in dimension_terms
                    ):
                        break
                else:
                    continue
                break
            else:
                return False
    haystack = " ".join(str(part or "") for part in (
        passage.get("text"), passage.get("title"), passage.get("source_url"), source.get("title"), source.get("url"),
        metadata.get("title"), metadata.get("source_url"), metadata.get("url"),
    )).casefold()
    if requirement.get("match_mode") == "all_components":
        for component in (requirement.get("entity_terms") or (), dimension_terms):
            if component and not all(
                any(candidate in haystack for candidate in (term, *_CONCEPT_EQUIVALENTS.get(term, ())))
                for term in component
            ):
                return False
    # Named Latin terms are anchors. Generic translated concepts alone must
    # not let an unrelated benchmarking article satisfy an Agent task.
    latin_anchors = [term for term in terms if re.fullmatch(r"[a-z][a-z0-9_.-]{2,}", term)
                     and term not in _STOP_TERMS]
    if latin_anchors and not any(term in haystack for term in latin_anchors):
        return False
    matches = sum(any(candidate in haystack for candidate in (term, *_CONCEPT_EQUIVALENTS.get(term, ())))
                  for term in terms)
    # A named English/CJK term is meaningful by itself; generic Chinese
    # bigrams require two hits to avoid accepting arbitrary demo prose.
    return matches >= (2 if len(terms) > 3 else 1)


def _task_terms(task: str) -> tuple[str, ...]:
    raw = re.findall(r"[a-zA-Z][a-zA-Z0-9_.-]{2,}|[\u4e00-\u9fff]{2,}", task.casefold())
    terms: list[str] = []
    for token in raw:
        if token in _STOP_TERMS:
            continue
        if re.fullmatch(r"[\u4e00-\u9fff]+", token) and len(token) > 4:
            # Whole Chinese clauses rarely occur verbatim in source text. Add
            # overlapping bigrams while retaining the whole named term.
            terms.append(token)
            terms.extend(token[index:index + 2] for index in range(len(token) - 1))
        else:
            terms.append(token)
    return tuple(dict.fromkeys(term for term in terms if term not in _STOP_TERMS))
