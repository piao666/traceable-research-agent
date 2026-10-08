"""Stable identities and the semantic (as opposed to execution) contract."""
from __future__ import annotations

import copy
import hashlib
import json
import re


def identity(prefix: str, *parts: object) -> str:
    value = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return prefix + hashlib.sha256(value.encode("utf-8")).hexdigest()[:56]


def semantic_contract(contract: dict | None) -> dict:
    payload = copy.deepcopy(contract or {})
    for key in ("controller_findings", "evidence_focus", "retained_cell_windows", "research_state", "answer_scope_attempt", "research_provider_failure"):
        payload.pop(key, None)
    scope = payload.get("comparison_scope")
    if isinstance(scope, dict):
        for key in ("selection_attempt", "selection_search_rounds", "selection"):
            scope.pop(key, None)
    answer_scope = payload.get("answer_scope")
    if isinstance(answer_scope, dict):
        answer_scope.pop("decision_audit", None)
    return payload


def canonical_name(display: str, quote: str = "", proposed: str = "") -> str:
    """Use an attested base label; never invent an alias by fuzzy matching."""
    for value in (proposed, display, re.split(r"\s*[（(]", display, maxsplit=1)[0]):
        value = value.strip()
        if value and (not quote or re.search(r"(?<![\w])" + re.escape(value) + r"(?![\w])", quote, re.I)):
            return value
    return display.strip()


def synthesis_contract(contract: dict) -> dict:
    """Present obligations without prior answer offsets or decision envelopes.

    Identity provenance stays in the persisted contract. Answer occurrence
    offsets belong to one draft and must never become another draft's input
    marker vocabulary.
    """
    payload = semantic_contract(contract)
    if contract.get("evidence_focus"):
        payload["evidence_focus"] = copy.deepcopy(contract["evidence_focus"])
    scope = payload.get("answer_scope")
    if isinstance(scope, dict):
        payload["answer_scope"] = {key: scope[key] for key in ("version", "entities") if key in scope}
    scope = payload.get("comparison_scope")
    if isinstance(scope, dict):
        scope.pop("entity_bindings", None)
    return payload
