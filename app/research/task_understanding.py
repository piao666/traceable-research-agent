"""Fail-closed merge boundary for generic, model-proposed task requirements.

Server-derived task constraints remain authoritative. This module only appends
validated research questions and evidence obligations; it cannot rewrite task,
source, date, or language constraints and does not decide evidence sufficiency.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any
import hashlib
import json
import re

from pydantic import ValidationError

from app.research.contracts import EvidenceRequirement, ResearchQuestion, requirement_index


_PROPOSAL_KEYS = {"questions", "requirements", "evidence_scope_requirements"}
_SCOPE_KEYS = {
    "requirement_id", "question_id", "entity", "dimension", "predicate",
    "time_scope", "source_scope", "match_mode", "min_independent_sources",
    "required", "acceptable_content_basis",
}
_SOURCE_SCOPES = {"external_web", "local_project", "unspecified"}
_MATCH_MODES = {"all_components", "unspecified"}


class TaskUnderstandingError(ValueError):
    """A model proposal cannot be safely merged into the task contract."""


def merge_task_understanding(
    server_contract: dict[str, Any], proposal: dict[str, Any]
) -> dict[str, Any]:
    """Append a validated model proposal without changing server-owned fields.

    IDs are exact opaque identifiers. Any duplicate, malformed ID, or dangling
    question/requirement reference rejects the whole proposal atomically.
    Existing obligations are never removed or weakened.
    """
    if not isinstance(server_contract, dict) or not isinstance(proposal, dict):
        raise TaskUnderstandingError("Contract and proposal must be objects")
    if set(proposal) - _PROPOSAL_KEYS:
        raise TaskUnderstandingError("Proposal contains unsupported fields")

    result = deepcopy(server_contract)
    try:
        known_requirements = requirement_index(server_contract)
    except (ValueError, TypeError) as exc:
        raise TaskUnderstandingError("Server contract has invalid requirement IDs") from exc

    existing_questions = _validated_existing_questions(server_contract)
    question_ids = {item.question_id for item in existing_questions}
    requirement_ids = set(known_requirements)

    questions = _as_list(proposal, "questions")
    requirements = _as_list(proposal, "requirements")
    scope_requirements = _as_list(proposal, "evidence_scope_requirements")
    if not (questions or requirements or scope_requirements):
        raise TaskUnderstandingError("Proposal must add at least one obligation")

    parsed_questions: list[ResearchQuestion] = []
    for raw in questions:
        try:
            item = ResearchQuestion.model_validate(raw)
        except (ValidationError, TypeError) as exc:
            raise TaskUnderstandingError("Invalid proposed question") from exc
        if item.question_id in question_ids:
            raise TaskUnderstandingError("Duplicate question ID")
        question_ids.add(item.question_id)
        parsed_questions.append(item)

    parsed_requirements: list[EvidenceRequirement] = []
    for raw in requirements:
        try:
            item = EvidenceRequirement.model_validate(raw)
        except (ValidationError, TypeError) as exc:
            raise TaskUnderstandingError("Invalid proposed requirement") from exc
        if item.requirement_id in requirement_ids:
            raise TaskUnderstandingError("Duplicate requirement ID")
        if not item.required or item.min_independent_sources < 1:
            raise TaskUnderstandingError("Proposed obligations must remain required and independently sourced")
        if not item.acceptable_content_basis or not set(item.acceptable_content_basis).issubset(
            {"full_text", "partial", "table", "structured"}
        ):
            raise TaskUnderstandingError("Proposed obligations must require body-bearing evidence")
        requirement_ids.add(item.requirement_id)
        parsed_requirements.append(item)

    parsed_scope: list[dict[str, Any]] = []
    for raw in scope_requirements:
        if not isinstance(raw, dict) or set(raw) - _SCOPE_KEYS:
            raise TaskUnderstandingError("Invalid evidence-scope requirement fields")
        identity = raw.get("requirement_id")
        if not isinstance(identity, str) or not identity.strip() or len(identity) > 160:
            raise TaskUnderstandingError("Invalid evidence-scope requirement ID")
        if identity in requirement_ids:
            raise TaskUnderstandingError("Duplicate requirement ID")
        requirement_ids.add(identity)
        question_id = raw.get("question_id")
        if question_id is not None and (
            not isinstance(question_id, str) or not question_id.strip() or len(question_id) > 160
        ):
            raise TaskUnderstandingError("Invalid evidence-scope question ID")
        if question_id is not None and question_id not in question_ids:
            raise TaskUnderstandingError("Unknown question ID")
        for field, allowed in (("source_scope", _SOURCE_SCOPES), ("match_mode", _MATCH_MODES)):
            value = raw.get(field)
            if value is not None and (not isinstance(value, str) or value not in allowed):
                raise TaskUnderstandingError(f"Invalid {field}")
        for field, max_length in (("entity", 500), ("dimension", 500), ("predicate", 2000),
                                  ("time_scope", 500)):
            value = raw.get(field)
            if value is not None and (
                not isinstance(value, str) or not value.strip() or len(value) > max_length
            ):
                raise TaskUnderstandingError(f"Invalid evidence-scope {field}")
        if "min_independent_sources" in raw and (
            type(raw["min_independent_sources"]) is not int
            or not 0 <= raw["min_independent_sources"] <= 100
        ):
            raise TaskUnderstandingError("Invalid independence threshold")
        if "required" in raw and type(raw["required"]) is not bool:
            raise TaskUnderstandingError("Invalid required flag")
        if "acceptable_content_basis" in raw:
            if not isinstance(raw["acceptable_content_basis"], (list, tuple)):
                raise TaskUnderstandingError("Invalid content-basis restriction")
            # Reuse the canonical vocabulary and normalization rules.
            try:
                checked = EvidenceRequirement.model_validate({
                    "requirement_id": identity,
                    "acceptable_content_basis": raw["acceptable_content_basis"],
                })
            except ValidationError as exc:
                raise TaskUnderstandingError("Invalid content-basis restriction") from exc
            raw = {**raw, "acceptable_content_basis": list(checked.acceptable_content_basis)}
        parsed_scope.append(deepcopy(raw))

    for item in parsed_questions:
        if any(ref not in requirement_ids for ref in item.requirement_ids):
            raise TaskUnderstandingError("Question references an unknown requirement ID")
    for item in parsed_requirements:
        if item.question_id not in question_ids:
            raise TaskUnderstandingError("Requirement references an unknown question ID")
    if not parsed_requirements:
        raise TaskUnderstandingError("Proposal must include at least one required body-evidence obligation")

    # Existing contract collections must be valid lists before append; malformed
    # server state is not repaired or silently overwritten by model output.
    for key, additions in (
        ("questions", [item.model_dump(mode="json") for item in parsed_questions]),
        ("requirements", [item.model_dump(mode="json") for item in parsed_requirements]),
        ("evidence_scope_requirements", parsed_scope),
    ):
        if additions:
            current = server_contract.get(key, [])
            if current is None:
                current = []
            if not isinstance(current, list):
                raise TaskUnderstandingError(f"Server {key} must be a list")
            result[key] = deepcopy(current) + additions
    return result


def _as_list(proposal: dict[str, Any], key: str) -> list[Any]:
    value = proposal.get(key, [])
    if not isinstance(value, list):
        raise TaskUnderstandingError(f"Proposal {key} must be a list")
    return value


def _validated_existing_questions(contract: dict[str, Any]) -> list[ResearchQuestion]:
    raw = contract.get("questions", [])
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise TaskUnderstandingError("Server questions must be a list")
    parsed: list[ResearchQuestion] = []
    seen: set[str] = set()
    for item in raw:
        try:
            question = ResearchQuestion.model_validate(item)
        except (ValidationError, TypeError) as exc:
            raise TaskUnderstandingError("Server contract has invalid questions") from exc
        if question.question_id in seen:
            raise TaskUnderstandingError("Server contract has duplicate question IDs")
        seen.add(question.question_id)
        parsed.append(question)
    return parsed


def understand_new_task(contract: dict[str, Any], client: Any) -> dict[str, Any]:
    """Derive obligations once at the trusted creation boundary, with literal anchors."""
    from app.llm.base import LLMMessage, LLMResponse
    from app.evidence.decision_audit import retain_decision
    original = str(contract.get("original_task") or "")
    from app.research.comparison_scope import comparison_spec, grounded_focus
    contract = {**contract, "comparison_scope": comparison_spec(original)}
    if contract.get("answer_mode") == "discovery":
        return contract
    if contract.get("requirements"):
        result = deepcopy(contract)
        if result.get("focus_version") != "literal-focus-v2":
            # Upgrade trusted persisted obligations without rewriting their IDs
            # or billing for another decomposition. Old predicates were made
            # literal; the retained search hints preserve the specific topic.
            focus = result.setdefault("requirement_focus", {})
            for r in result["requirements"]:
                rid = r["requirement_id"]
                if rid == "req-original":
                    continue
                hint = (result.get("research_terms") or {}).get(rid)
                if hint:
                    focus[rid] = grounded_focus(str(r.get("predicate") or original),
                        {**r, "entity": hint}, result["comparison_scope"])
            result["focus_version"] = "literal-focus-v2"
            result["focus_upgrade_audit"] = retain_decision("requirement_focus_upgrade",
                {"original_task": original, "previous_focus": contract.get("requirement_focus"),
                 "research_terms": result.get("research_terms")}, {"requirement_focus": focus})
        return _with_original_obligation(result, original)
    proposal = None
    response = client.structured_complete([
        LLMMessage(role="system", content=(
            "Decompose every explicit user question into mandatory research obligations. Return JSON only "
            "with questions and requirements arrays. Each question has question_id, text, requirement_ids; "
            "text MUST be an exact contiguous substring of the original task. Each requirement has "
            "requirement_id, question_id, kind (fact/causal/comparison/identity/metric/scope), predicate "
            "restating the user question, NEVER asserting or guessing an answer or adding new research topics, "
            "required:true, min_independent_sources:1, acceptable_content_basis:[full_text,partial]. "
            "Set entity to concise English search keywords translating the anchored question's topic, without assertions. "
            "Split explicit conjunctive questions such as whether X and when X into separate exact original-text anchors, "
            "with English search keywords specific to each question. Do not add unrequested questions or weaken constraints. Include every requested factual aspect. "
            "Instructions about output language, citation format or report layout are presentation constraints, not questions needing source evidence.")),
        LLMMessage(role="user", content=original)], temperature=0, max_tokens=4000) if client is not None else LLMResponse(
            success=False, error_message="Task understanding provider is unavailable.", provider="unavailable")
    audit = retain_decision("task_understanding_decision", {"original_task": original}, response.model_dump(), record_usage=True)
    try:
        if not response.success:
            raise TaskUnderstandingError("Task understanding provider failed")
        proposal = json.loads(response.content)
        result = merge_task_understanding(contract, proposal)
        for question in proposal["questions"]:
            if question["text"] not in original:
                raise TaskUnderstandingError("Question lacks an original-task anchor")
        result = _split_explicit_compound_questions(result)
        presentation = [q for q in result["questions"] if _is_presentation_only(q["text"])]
        if presentation and len(presentation) < len(result["questions"]):
            presentation_ids = {q["question_id"] for q in presentation}
            result["presentation_instructions"] = [q["text"] for q in presentation]
            result["questions"] = [q for q in result["questions"] if q["question_id"] not in presentation_ids]
            result["requirements"] = [r for r in result["requirements"] if r["question_id"] not in presentation_ids]
            result["evidence_scope_requirements"] = [r for r in result.get("evidence_scope_requirements", [])
                if r.get("question_id") not in presentation_ids]
        anchors = [{"question_id": q["question_id"], "start": original.index(q["text"]),
                    "end": original.index(q["text"]) + len(q["text"])} for q in result["questions"]]
        if audit["redaction_changed"]:
            raise TaskUnderstandingError("Task understanding audit was redacted")
        # The user's literal questions define the obligations. Model-generated
        # explanations may invent facts or introduce additional topics, so they
        # cannot become mandatory predicates. Preserve every obligation under
        # an anchored question; question_id is a one-to-many relationship.
        by_question: dict[str, list[dict[str, Any]]] = {}
        for requirement in result["requirements"]:
            by_question.setdefault(requirement["question_id"], []).append(requirement)
        normalized = []
        result["research_terms"] = {}
        result["requirement_focus"] = {}
        for question in result["questions"]:
            obligations = by_question.get(question["question_id"], [])
            if not obligations:
                raise TaskUnderstandingError("Question lacks a required obligation")
            for requirement in obligations:
                result["research_terms"][requirement["requirement_id"]] = str(requirement.get("entity") or "")[:500]
                result["requirement_focus"][requirement["requirement_id"]] = grounded_focus(
                    question["text"], requirement, result["comparison_scope"])
                normalized.append({**requirement, "predicate": question["text"], "entity": None, "dimension": None, "time_scope": None})
            question["requirement_ids"] = [r["requirement_id"] for r in obligations]
        result["requirements"] = normalized
    except (ValueError, KeyError, TypeError) as exc:
        # Preserve the entire obligation rather than silently returning an empty contract.
        result = deepcopy(contract)
        result["questions"] = [{"question_id": "q-original", "text": original,
                                "requirement_ids": ["req-original"]}]
        result["requirements"] = [EvidenceRequirement(requirement_id="req-original",
            question_id="q-original", predicate=original).model_dump(mode="json")]
        result["understanding_fallback"] = type(exc).__name__
        anchors = [{"question_id": "q-original", "start": 0, "end": len(original)}]
    result["obligation_version"] = "research-obligations-v2"
    result["focus_version"] = "literal-focus-v2"
    result["question_anchors"] = anchors
    result["understanding_audit"] = audit
    result["original_task_sha256"] = hashlib.sha256(original.encode("utf-8")).hexdigest()
    result = _with_original_obligation(result, original)
    result["understanding_application_audit"] = retain_decision("task_understanding_application",
        {"original_task": original}, {"questions": result["questions"], "requirements": result["requirements"],
        "question_anchors": result["question_anchors"], "fallback": result.get("understanding_fallback"),
        "research_terms": result.get("research_terms", {}), "requirement_focus": result.get("requirement_focus", {}),
        "comparison_scope": result.get("comparison_scope", {})},
        parent=audit["decision_sha256"])
    return result


def _split_explicit_compound_questions(result: dict[str, Any]) -> dict[str, Any]:
    """Keep conjunctive explicit questions independently mandatory, using exact anchors."""
    questions, requirements = [], []
    by_question: dict[str, list[dict[str, Any]]] = {}
    for requirement in result["requirements"]:
        by_question.setdefault(requirement["question_id"], []).append(requirement)
    boundary = re.compile(r"(?:以及|及|并且|并|和|\band\s+)(?=(?:为什么|为何|何时|什么时候|如何|是否|when\b|why\b|how\b|whether\b))", re.I)
    for q in result["questions"]:
        parts = [text.strip() for text in boundary.split(q["text"]) if text.strip()]
        obligations = by_question[q["question_id"]]
        if len(parts) == 1:
            questions.append({**q, "requirement_ids": [r["requirement_id"] for r in obligations]})
            requirements.extend(obligations)
            continue
        for i, text in enumerate(parts):
            qid = q["question_id"] if i == 0 else "q-part-" + hashlib.sha256(f"{q['question_id']}|{i}|{text}".encode()).hexdigest()[:24]
            ids = []
            for required in obligations:
                rid = required["requirement_id"] if i == 0 else "req-part-" + hashlib.sha256(f"{required['requirement_id']}|{i}|{text}".encode()).hexdigest()[:24]
                ids.append(rid)
                requirements.append({**required, "question_id": qid, "requirement_id": rid})
            questions.append({**q, "question_id": qid, "text": text, "requirement_ids": ids})
    result["questions"], result["requirements"] = questions, requirements
    return result


def _is_presentation_only(text: str) -> bool:
    """Recognize stand-alone answer-format clauses, never domain questions."""
    clauses = [part.strip() for part in re.split(r"[，,；;。]", text) if part.strip()]
    if not clauses:
        return False
    zh_citation = re.compile(
        r"^(?:请)?(?:逐项|分别|每项|每个问题)?(?:给出|提供|标注|附上)"
        r"(?:正文|原文|可核验的)?(?:引用|出处|来源|链接)(?:标记)?$"
    )
    zh_language = re.compile(r"^(?:请)?(?:用|以)(?:中文|英文)(?:作答|回答|输出|撰写)?$")
    zh_separate = re.compile(r"^(?:请)?[\u3400-\u9fff、和与以及\s]{2,100}分别回答$")
    en_format = re.compile(
        r"^(?:please\s+)?(?:answer\s+in\s+(?:chinese|english)|"
        r"(?:provide|include|cite)\s+(?:source\s+)?(?:citations?|sources?|links?)"
        r"(?:\s+for\s+each\s+(?:answer|item))?)$", re.I,
    )
    def facet_directive(part: str) -> bool:
        rest = re.sub(r"^(?:请)?(?:说明|明确|阐明)", "", part)
        facets = [value.strip() for value in re.split(r"[、和与及]", rest) if value.strip()]
        return rest != part and len(facets) >= 2 and set(facets) <= {
            "机制", "原理", "条件", "适用条件", "限制", "边界",
        }
    return all(zh_citation.fullmatch(part) or zh_language.fullmatch(part)
               or zh_separate.fullmatch(part) or facet_directive(part) or en_format.fullmatch(part)
               for part in clauses)


def _with_original_obligation(result: dict[str, Any], original: str) -> dict[str, Any]:
    # A model may overlook a clause while producing a syntactically valid
    # decomposition. The original user request is an independent, required
    # aggregate obligation, so such omissions remain blocking.
    if not any(r.get("requirement_id") == "req-original" for r in result.get("requirements", [])):
        result.setdefault("questions", []).append({"question_id": "q-original", "text": original,
            "requirement_ids": ["req-original"]})
        result.setdefault("requirements", []).append(EvidenceRequirement(requirement_id="req-original",
            question_id="q-original", predicate=original, acceptable_content_basis=("full_text", "partial")).model_dump(mode="json"))
        result.setdefault("question_anchors", []).append({"question_id": "q-original", "start": 0, "end": len(original)})
    result["obligation_version"] = "research-obligations-v2"
    result["original_task_sha256"] = hashlib.sha256(original.encode("utf-8")).hexdigest()
    return result
