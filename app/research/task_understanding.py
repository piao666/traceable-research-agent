"""Fail-closed merge boundary for generic, model-proposed task requirements.

Server-derived task constraints remain authoritative. This module only appends
validated research questions and evidence obligations; it cannot rewrite task,
source, date, or language constraints and does not decide evidence sufficiency.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

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
