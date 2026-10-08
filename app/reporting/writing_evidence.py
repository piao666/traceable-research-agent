"""A narrow, immutable projection of provenance for report writing.

This is intentionally not a retrieval abstraction.  It only decides which
already persisted passages may be shown to a report writer in one frozen
attempt.  Discovery records remain available for an index, never as factual
prompt material.
"""

from __future__ import annotations

import hashlib
import json
import math
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
    projection_diagnostics: tuple[dict[str, Any], ...] = ()

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
        if (contract or {}).get("source_constraints", {}).get("official_only") and not (
            dmeta.get("official") is True or dmeta.get("source_class") in {"official", "official_code", "regulatory"}
        ):
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
    projection_diagnostics: list[dict[str, Any]] = []
    projected_windows: set[tuple[str, str]] = set()
    terms = _contract_terms(contract)
    work_focus = (contract or {}).get("evidence_focus") or []
    # Cell targets must remain separate. Counting all mentioned products
    # rewards directories and comparison boilerplate over the acquired answer.
    focus_entities = [str(c["entity"]).casefold() for c in work_focus if c.get("entity")]
    pending = list(candidates)
    modern = bool((contract or {}).get("obligation_version"))
    # Document frequency discounts page-wide boilerplate. Inflection variants
    # let a question about a writer match source sentences about writers.
    frequencies: dict[str, int] = {}
    for item in candidates:
        for token in _projection_tokens(item[5]):
            frequencies[token] = frequencies.get(token, 0) + 1
    candidate_count = len(candidates)
    requirements = [r for r in (contract or {}).get("requirements", []) if r.get("requirement_id") != "req-original"]
    def requirement_query(requirement: dict[str, Any]) -> str:
        rid = requirement.get("requirement_id")
        target = (contract or {}).get("requirement_focus", {}).get(rid, {})
        hints = str((contract or {}).get("research_terms", {}).get(rid) or "")
        if target.get("facet") == "selection":
            from app.research.comparison_scope import selection_query
            return selection_query(contract or {})
        if target.get("facet") and (contract or {}).get("comparison_scope"):
            return " ".join(str(v or "") for v in (target.get("entity"), target.get("dimension"), target["facet"], hints))
        return str(requirement.get("predicate") or "") + " " + hints
    query_sets = [_projection_tokens(requirement_query(r)) for r in requirements]
    shared_terms = {t for q in query_sets for t in q if sum(t in other for other in query_sets) > len(query_sets) / 2} if len(query_sets) > 1 else set()
    window_cache: dict[str, list[tuple[int, int, str]]] = {}
    token_cache: dict[str, set[str]] = {}
    def window_tokens(value: str) -> set[str]:
        if value not in token_cache:
            token_cache[value] = _projection_tokens(value)
        return token_cache[value]
    def substantive_windows(value: str):
        if value not in window_cache:
            window_cache[value] = _substantive_windows(value)
        return window_cache[value]
    def score(value: str, query: set[str]) -> tuple[int, float, float, int]:
        if not modern:
            return 0, _relevance(value, query)[0], 0, -len(value)
        hits = window_tokens(value) & query
        def weighted(items: set[str]) -> float:
            return sum(math.log1p(candidate_count / (1 + frequencies.get(t, 0))) ** 2 for t in items)
        specific = query - shared_terms
        asks_for_cases = bool(query & {"when", "cases", "case", "conditions", "condition", "时候", "何时", "情况", "条件"})
        conditions = min(8, len(re.findall(r"\b(?:if|when|unless|while)\b", value, re.I))) if asks_for_cases else 0
        if asks_for_cases and conditions >= 2 and re.search(r"\b(?:include|includes|are|is)\s+the\s+following\s*:", value, re.I):
            conditions += 2
        relevant = hits & specific if specific else hits
        return conditions if relevant else 0, weighted(relevant), weighted(hits), -len(value)
    def best_score(item: Any, query: set[str]) -> tuple[int, float, float, int]:
        return max((score(value, query) for _, _, value in substantive_windows(item[5])), default=(0, 0, 0, 0))
    question_terms_by_citation: dict[str, set[str]] = {}
    cell_windows: dict[str, tuple[int, int, str]] = {}
    cell_by_citation: dict[str, dict[str, Any]] = {}
    retained_labels: set[str] = set()
    if (contract or {}).get("obligation_version"):
        from app.research.answer_coverage import _coverage_constraints
        constraints = _coverage_constraints(contract, requirements, (contract.get("answer_scope") or {}).get("entities"))
        cells = []
        for requirement in requirements:
            for cell in constraints["cells"][requirement["requirement_id"]]:
                if not any(c["entity"] == cell["entity"] and c["facet"] == cell["facet"] for c in cells):
                    cells.append({"requirement_id": requirement["requirement_id"], **cell})
        # Focus can exist before a referential inventory has been frozen.
        for cell in work_focus:
            if cell.get("entity") and not any(c["entity"] == cell["entity"] and c["facet"] == cell.get("facet") for c in cells):
                cells.append(cell)
        cells = [c for c in cells if not str(c.get("facet") or "").startswith("selection")]
        ordered = []
        preserved_cells = set()
        cell_quota = max(160, (token_limit - used_tokens) // max(1, len(cells))) if token_limit is not None else None
        cell_costs: dict[tuple[str, str], int] = {}
        for retained in contract.get("retained_cell_windows") or []:
            cell = retained.get("target") or {}
            if not any(c["entity"] == cell.get("entity") and c["facet"] == cell.get("facet") for c in cells):
                continue
            item = next((i for i in pending + ordered if i[1].get("passage_id") == retained.get("passage_id")
                and hashlib.sha256(i[5].encode()).hexdigest() == retained.get("passage_sha256")), None)
            start, end = retained.get("start"), retained.get("end")
            if item is None or type(start) is not int or type(end) is not int or not 0 <= start < end <= len(item[5]):
                continue
            value = item[5][start:end]
            if hashlib.sha256(value.encode()).hexdigest() != retained.get("text_sha256"):
                continue
            label = str(item[0].get("citation_label"))
            if label in cell_windows:
                old = cell_windows[label]
                start, end = min(old[0], start), max(old[1], end)
                value = item[5][start:end]
            cost = estimate_text_tokens(value) + 100
            cell_key = (cell.get("entity"), cell.get("facet"))
            if cell_quota is not None and cell_costs.get(cell_key, 0) + cost > cell_quota:
                continue
            retained_cost = sum(estimate_text_tokens(w[2]) + 100 for w in cell_windows.values())
            replaced_cost = estimate_text_tokens(cell_windows[label][2]) + 100 if label in cell_windows else 0
            if token_limit is not None and retained_cost - replaced_cost + cost > max(0, token_limit - used_tokens):
                continue
            if item in pending:
                pending.remove(item)
                ordered.append(item)
            cell_windows[label] = (start, end, value)
            cell_by_citation[label] = cell
            retained_labels.add(label)
            cell_costs[cell_key] = cell_costs.get(cell_key, 0) + cost
            preserved_cells.add((cell.get("entity"), cell.get("facet")))
        cells = [c for c in cells if (c["entity"], c["facet"]) not in preserved_cells]
        slots = cells + [c for c in cells if c["facet"] in {"mechanism", "framework", "memory"}]
        preserved_tokens = sum(estimate_text_tokens(w[2]) + 100 for w in cell_windows.values())
        per_cell_tokens = max(160, (token_limit - used_tokens - preserved_tokens) // max(1, len(slots))) if token_limit is not None else None
        cell_sources: dict[tuple[str, str], set[str]] = {}
        for cell in slots:
            facet_terms = _projection_tokens(_CELL_TERMS.get(cell["facet"], cell["facet"]))
            entity = cell["entity"]
            def cell_score(item, window):
                value = window[2]
                named = bool(re.search(r"(?<!\w)" + re.escape(entity) + r"(?!\w)", value, re.I))
                matches = len(window_tokens(value) & facet_terms)
                snapshot = item[2]
                document = documents.get(str(snapshot.get("document_id") or "")) or {}
                metadata = {**document.get("metadata", {}), **snapshot.get("metadata", {})}
                primary = metadata.get("official") is True or metadata.get("source_class") in {"official", "official_code", "regulatory"}
                trace_id = str(item[0].get("origin_trace_id") or item[1].get("origin_trace_id") or item[1].get("trace_id") or "")
                acquired = any(f.get("entity") == entity and f.get("facet") == cell["facet"] and
                    trace_id in f.get("trace_ids", []) for f in work_focus)
                # Ranking admits context; it never grants semantic support.
                return (int(named and matches > 0), int(primary and cell["facet"] in {"mechanism", "framework", "memory"}),
                        int(acquired), int(named), matches, score(value, facet_terms), -len(value))
            from urllib.parse import urlparse
            key = (cell["entity"], cell["facet"])
            used_sources = cell_sources.setdefault(key, set())
            options = [(item, window) for item in pending for window in substantive_windows(item[5])
                if (urlparse(item[6] or "").hostname or "") not in used_sources
                and (per_cell_tokens is None or estimate_text_tokens(window[2]) + 100 <= per_cell_tokens)]
            # One source table may answer several cells. Reuse its marker only
            # by expanding the SAME exact slice while preserving earlier rows.
            for item in ordered:
                label = str(item[0].get("citation_label"))
                old = cell_windows.get(label)
                if old is None:
                    continue
                expanded = [w for w in substantive_windows(item[5]) if w[0] <= old[0] and w[1] >= old[1]
                    and (per_cell_tokens is None or estimate_text_tokens(w[2]) + 100 <= per_cell_tokens)]
                options.extend((item, w) for w in expanded if (urlparse(item[6] or "").hostname or "") not in used_sources)
            if not options:
                continue
            item, window = max(options, key=lambda pair: cell_score(*pair))
            if cell_score(item, window)[0] == 0:
                projection_diagnostics.append({"cause": "cell_window_missing", "target": cell,
                    "grants_answer_coverage": False})
                continue
            if item in pending:
                pending.remove(item)
                ordered.append(item)
            used_sources.add(urlparse(item[6] or "").hostname or "")
            label = str(item[0].get("citation_label"))
            cell_windows[label] = window
            cell_by_citation[label] = cell
            question_terms_by_citation[label] = facet_terms | _projection_tokens(entity)
        # Admit one relevant passage per obligation before additional material.
        for requirement in ((contract or {}).get("requirements") or []) * 2:
            if requirement.get("requirement_id") == "req-original":
                continue  # Aggregate coverage reuses the individual question evidence.
            query_terms = _projection_tokens(requirement_query(requirement))
            if pending:
                best = max(pending, key=lambda item: best_score(item, query_terms))
                pending.remove(best)
                ordered.append(best)
                question_terms_by_citation[str(best[0].get("citation_label"))] = query_terms
        pending = ordered + sorted(pending, key=lambda item: best_score(item, terms), reverse=True)
    while pending:
        citation, passage, snapshot, role, basis, text, url = pending.pop(0)
        # Feedback only ranks slices of an already admitted immutable passage.
        # It never supplies evidence text or makes a citation supported.
        focus = (focus_by_citation or {}).get(str(citation.get("citation_label")))
        window_terms = (_projection_tokens if modern else _lexical_tokens)(focus[:2000]) if isinstance(focus, str) and focus.strip() else question_terms_by_citation.get(str(citation.get("citation_label")), terms)
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
        if modern:
            candidates = substantive_windows(text)
        overhead = estimate_text_tokens(json.dumps({
            "citation_id": citation.get("citation_label"), "source_url": url,
            "evidence_role": role, "content_basis": basis, "text": "",
        }, ensure_ascii=False)) + 24
        eligible = [item for item in candidates if remaining is None or estimate_text_tokens(item[2]) + overhead <= remaining]
        relevant = [
            item for item in eligible
            if window_terms and _relevance(item[2], window_terms)[0] > 0
        ]
        label = str(citation.get("citation_label"))
        if label in cell_windows and cell_windows[label] in eligible:
            chosen = cell_windows[label]
        elif relevant:
            # A focused, matching sentence is more useful than a page-sized
            # parent with the same one matching term.  Ties prefer the smaller
            # exact window; the source hashes and offsets are validated later.
            chosen = max(
                relevant,
                key=lambda item: score(item[2], window_terms),
            )
        else:
            chosen = max(eligible, key=lambda item: score(item[2], window_terms), default=None)
        projected = chosen[2] if chosen else ""
        if not projected and not (contract or {}).get("obligation_version") and (remaining is None or estimate_text_tokens(text) + overhead <= remaining):
            projected = text
        if not projected:
            projection_diagnostics.append({"citation_id": citation.get("citation_label"), "passage_id": passage.get("passage_id"),
                "cause": "projection_budget_exceeded" if candidates and not eligible else "window_context_missing",
                "candidate_windows": len(candidates), "eligible_windows": len(eligible), "remaining_tokens": remaining})
            continue
        window_key = (url or "", hashlib.sha256(projected.encode("utf-8")).hexdigest())
        if modern and url and window_key in projected_windows:
            continue
        projected_windows.add(window_key)
        projection_diagnostics.append({"citation_id": citation.get("citation_label"), "passage_id": passage.get("passage_id"),
            "cause": "selected_target_window" if focus_entities else "selected_requirement_window",
            "focus": work_focus, "candidate_windows": len(candidates), "eligible_windows": len(eligible),
            "target": cell_by_citation.get(label), "grants_answer_coverage": False,
            "retained_candidate": label in retained_labels,
            "selected_start": chosen[0] if chosen else 0, "selected_end": chosen[1] if chosen else len(projected),
            "ranking_score": list(score(projected, window_terms)), "remaining_tokens": remaining})
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
    return WritingEvidenceSet(tuple(factual), tuple(discovery), tuple(unit.citation_id for unit in factual), tuple(gaps),
        projection_diagnostics=tuple(projection_diagnostics))


# Query vocabulary is used only for choosing exact retained slices. Semantic
# coverage and citation support are independently checked after writing.
_CELL_TERMS = {
    "application": "use case example task customer support math solve coding research workflow 应用 场景 客服 数学 编程 任务",
    "memory": "memory memories remember persist history context session storage retrieval 记忆 持久 历史 上下文 会话 存储",
    "framework": "architecture framework runtime orchestration planner executor tools 架构 框架 运行 编排 规划 执行 工具",
    "mechanism": "mechanism planning execution feedback reason action context tool 原理 机制 规划 执行 反馈 推理 上下文 工具",
    "evaluation": "benchmark evaluation score experiment accuracy performance failure 评测 基准 分数 实验 准确 性能 失败",
}


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
    values.extend(str(value) for value in (contract or {}).get("research_terms", {}).values())
    return (_projection_tokens if (contract or {}).get("obligation_version") else _lexical_tokens)(" ".join(values))


def _lexical_tokens(text: str) -> set[str]:
    tokens = {token.casefold() for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", text)}
    for sequence in re.findall(r"[\u3400-\u9fff]+", text):
        if len(sequence) >= 2:
            tokens.update(sequence[i:i + 2] for i in range(len(sequence) - 1))
            if len(sequence) <= 8:
                tokens.add(sequence)
    return tokens


def _projection_tokens(text: str) -> set[str]:
    """Tokenize Latin words plus CJK bigrams for compact relevance matching."""
    tokens = {
        token.casefold()
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", text)
    }
    if "single" in tokens or "one" in tokens:
        tokens.update(("single", "one"))
    for token in list(tokens):
        root = token[:-1] if token.endswith("s") and len(token) > 4 else token
        tokens.add(root)
        for suffix in ("ing", "ed", "er"):
            if root.endswith(suffix) and len(root) > len(suffix) + 3:
                stem = root[:-len(suffix)]
                tokens.update((stem, stem + "e"))
    words = re.findall(r"[A-Za-z][A-Za-z0-9_-]*", text.casefold())
    stop = {"the", "and", "a", "at", "of", "can", "is", "only", "to", "be", "that", "in", "as", "by", "with", "are", "for", "there", "because"}
    roots = []
    for word in words:
        if word in stop:
            continue
        word = "one" if word == "single" else word
        if word.endswith("s") and len(word) > 4:
            word = word[:-1]
        for suffix in ("ing", "ed", "er"):
            if word.endswith(suffix) and len(word) > len(suffix) + 3:
                word = word[:-len(suffix)]
                break
        if word.endswith("e") and len(word) > 4:
            word = word[:-1]
        roots.append(word)
    tokens.update("pair:" + left + ":" + right for left, right in zip(roots, roots[1:]))
    for sequence in re.findall(r"[\u3400-\u9fff]+", text):
        if len(sequence) >= 2:
            tokens.update(sequence[index:index + 2] for index in range(len(sequence) - 1))
            if len(sequence) <= 8:
                tokens.add(sequence)
    return tokens


def _substantive_windows(text: str) -> list[tuple[int, int, str]]:
    """Exact sentence context; no rephrasing, inference or synthetic source text."""
    result = []
    # Tables are evidence-bearing structure. Preserve the column names and
    # the target row in one exact slice, including preceding rows if needed.
    # A row without headers loses the relationship between object and value.
    for table in re.finditer(r"(?m)^\s*\|[^\n]+\|\s*\r?\n\s*\|[ :|\-]+\|[^\n]*(?:\r?\n\s*\|[^\n]+\|[^\n]*)+", text):
        for row in re.finditer(r"(?m)^\s*\|[^\n]+\|[^\n]*", table.group(0)):
            end = table.start() + row.end()
            if end - table.start() <= 12000:
                result.append((table.start(), end, text[table.start():end]))
    sentences = _sentence_windows(text)
    for start, end, sentence in sentences:
        if len(sentence) < (16 if re.search(r"[\u3400-\u9fff]", sentence) else 40) or re.match(r"\s*(?:#{1,6}\s|!\[)", sentence):
            continue
        # Keep nearby process steps and premises, bounded independently of a
        # large raw-HTML paragraph. The anchor still determines relevance.
        left = min((a for a, _, _ in sentences if max(0, start - 500) <= a <= start), default=start)
        right = max((b for _, b, _ in sentences if end <= b <= end + 450), default=end)
        if re.search(r"\b(?:therefore|hence|consequently)\b", text[left:right], re.I):
            premises = [(a, b) for a,b,value in sentences if max(0, start - 3200) <= a <= left
                        and re.search(r"\b(?:if|when|unless)\b", value, re.I)
                        and re.search(r"\b[A-Za-z_][\w.]*\s*(?:=|(?:is\s+)?set\s+to)\s*\w+", value, re.I)]
            if premises:
                left = premises[-1][0]
        result.append((left, right, text[left:right]))
    # Flattened documentation often expresses a complete set of cases as one
    # colon-led list. Keep the source's whole bounded enumeration so a writer
    # can answer "when" with all stated conditions instead of one last case.
    for marker in re.finditer(r"\b(?:include|includes|are|is)\s+the\s+following\s*:", text, re.I):
        preceding = next((a for a, b, _ in sentences if a <= marker.start() < b), marker.start())
        next_heading = re.search(r"(?<!\S)\d{1,2}\.\s+[A-Z]", text[marker.end():])
        bound = min(len(text), preceding + 3000)
        if next_heading:
            bound = min(bound, marker.end() + next_heading.start())
        ending = max((b for _, b, _ in sentences if marker.end() < b <= bound), default=0)
        if ending > marker.end() and ending - preceding <= 3000:
            result.append((preceding, ending, text[preceding:ending]))
    return result


def _sentence_windows(text: str) -> list[tuple[int, int, str]]:
    """Return whitespace-trimmed, exact parent slices at sentence boundaries."""
    windows: list[tuple[int, int, str]] = []
    for match in re.finditer(r"[^\r\n]+?(?:[.!?](?=\s|$)|[。！？]|(?=\r?\n)|$)", text):
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
