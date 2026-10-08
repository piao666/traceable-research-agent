"""Database authority for the research work loop, shared by both controllers.

Tool success records an acquisition effect. Only a verified mapping to the
current final report can confirm an answer. Generic mapping failures preserve
previous concrete gaps instead of replacing them with an empty assessment.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from app.research.models import ResearchEntity, ResearchWorkItem, ResearchOperation
from app.research.state import identity


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _entity(db, root, name, candidate=None):
    candidate = candidate or {}
    prior = list(db.scalars(select(ResearchEntity).where(ResearchEntity.root_run_id == root, ResearchEntity.canonical_name == name)))
    if not candidate and len(prior) == 1:
        return prior[0]
    if not candidate and len(prior) > 1:
        raise ValueError("Research entity version is ambiguous")
    eid = identity("ent-", root, name.casefold(), candidate.get("version") or "")
    entity = db.get(ResearchEntity, eid)
    if entity is None:
        entity = ResearchEntity(entity_id=eid, root_run_id=root, canonical_name=name,
            display_name=candidate.get("display_name") or candidate.get("name") or name,
            aliases_json=_json(list(dict.fromkeys([name, candidate.get("display_name") or name]))),
            provenance_json=_json(candidate))
        db.add(entity)
        db.flush()
    elif candidate and entity.provenance_json in {"", "{}"}:
        entity.display_name = candidate.get("display_name") or candidate.get("name") or name
        entity.aliases_json = _json(list(dict.fromkeys([name, entity.display_name])))
        entity.provenance_json = _json(candidate)
    return entity


def ensure_work(db, root, requirement, entity="", facet="answer"):
    eid = _entity(db, root, entity).entity_id if entity else None
    wid = identity("work-", root, requirement, eid, facet)
    row = db.get(ResearchWorkItem, wid)
    if row is None:
        row = ResearchWorkItem(work_item_id=wid, root_run_id=root,
            requirement_id=requirement, entity_id=eid, facet=facet)
        db.add(row)
        db.flush()
    return row


def sync_work(db, root, plan):
    from app.research.answer_coverage import _coverage_constraints
    from app.research.contracts import normalize_requirements
    contract = plan.get("task_contract") or {}
    if not contract.get("obligation_version"):
        return []
    spec = contract.get("comparison_scope") or {}
    requirements = [r.model_dump(mode="json") for r in normalize_requirements(contract) if r.required]
    inventory = (contract.get("answer_scope") or {}).get("entities") or []
    constraints = _coverage_constraints(contract, requirements, inventory)
    for candidate in (spec.get("selection") or {}).get("candidates", []):
        _entity(db, root, candidate.get("canonical_name") or candidate.get("name"), candidate)
    for binding in (contract.get("answer_scope") or {}).get("bindings", []):
        _entity(db, root, binding["canonical_name"], {**binding,
            "decision_audit": contract["answer_scope"].get("decision_audit"),
            "binding_audit": contract["answer_scope"].get("binding_audit")})
    active = set()
    if spec.get("selection_required"):
        from app.research.comparison_scope import selection_requirement_ids
        row = ensure_work(db, root, selection_requirement_ids(contract)[0], facet="selection")
        active.add(row.work_item_id)
        if spec.get("entities"):
            row.acquisition_status = "qualified"
            row.answer_status = "acquired"
            row.reason_code = "final_answer_pending"
            row.evidence_refs_json = _json(spec.get("selection") or {})
        else:
            row.reason_code = "comparison_selection_missing"
    for requirement in requirements:
        rid = requirement["requirement_id"]
        cells = constraints["cells"][rid] or [{"entity": "", "facet": "answer"}]
        for cell in cells:
            row = ensure_work(db, root, rid, cell["entity"], cell["facet"])
            active.add(row.work_item_id)
    # Supersede only empty placeholders after a grounded cohort expansion.
    # Named mandatory cells are never removed by a writer changing its list.
    for row in db.scalars(select(ResearchWorkItem).where(ResearchWorkItem.root_run_id == root)):
        if row.work_item_id not in active and row.entity_id is None and row.facet == "answer":
            row.answer_status = "superseded"
        if spec.get("entities") and row.facet.startswith("selection_"):
            # Candidate lookup is a prerequisite hypothesis, not a mandatory
            # final object. Keep its journal after the cohort is frozen.
            row.answer_status = "superseded"
    db.flush()
    project_work(db, root, plan)
    return open_work(db, root)


def project_work(db, root, plan=None):
    entities = {e.entity_id: e for e in db.scalars(select(ResearchEntity).where(ResearchEntity.root_run_id == root))}
    rows = []
    operations = list(db.scalars(select(ResearchOperation).where(ResearchOperation.root_run_id == root).order_by(ResearchOperation.created_at)))
    for row in db.scalars(select(ResearchWorkItem).where(ResearchWorkItem.root_run_id == root).order_by(ResearchWorkItem.work_item_id)):
        entity = entities.get(row.entity_id)
        rows.append({"work_item_id": row.work_item_id, "requirement_id": row.requirement_id,
            "entity_id": row.entity_id, "entity": entity.canonical_name if entity else "", "facet": row.facet,
            "acquisition_status": row.acquisition_status, "answer_status": row.answer_status,
            "reason_code": row.reason_code, "detail": row.detail, "state_version": row.state_version,
            "evidence_refs": json.loads(row.evidence_refs_json or "{}"),
            "actions": [{"operation_id": o.operation_id, "kind": o.operation_kind, "status": o.status,
                         **json.loads(o.payload_json or "{}")} for o in operations
                        if json.loads(o.payload_json or "{}").get("work_item_id") == row.work_item_id]})
    if plan is not None:
        plan["research_work"] = {"version": "research-work-v1", "items": rows}
    return rows


def open_work(db, root):
    return [r for r in project_work(db, root) if r["answer_status"] not in {"confirmed", "superseded"}]


def apply_coverage(db, root, coverage, *, final_verified=False):
    """Keep per-cell progress; invalid/missing mappings cannot erase gaps."""
    entries = {r.get("requirement_id"): r for r in coverage.get("requirements", [])}
    gaps = coverage.get("gaps") or []
    digest = coverage.get("answer_sha256")
    for gap in gaps:
        if gap.get("requirement_id") and gap.get("entity"):
            ensure_work(db, root, gap["requirement_id"], gap["entity"], gap.get("facet") or "answer")
    for item in project_work(db, root):
        row = db.get(ResearchWorkItem, item["work_item_id"])
        if row.answer_status == "superseded":
            continue
        entry = entries.get(row.requirement_id, {})
        applicable = [g for g in gaps if g.get("requirement_id") == row.requirement_id and
            (not g.get("entity") or g.get("entity") == item["entity"]) and
            (not g.get("facet") or g.get("facet") == item["facet"])]
        concrete = [g for g in applicable if g.get("cause") != "coverage_mapping_missing"]
        mapped = next((c for c in entry.get("comparison_cells", []) if c.get("entity") == item["entity"]
                       and c.get("facet") == item["facet"]), entry if not item["entity"] else {})
        if concrete:
            gap = concrete[0]
            if not (row.facet.startswith("selection_") and gap.get("cause") == "comparison_selection_missing"):
                row.reason_code = gap.get("cause") or "answer_content_missing"
                row.detail = str(gap.get("detail") or entry.get("reason") or "")
            row.answer_status = "unanswered"
        elif mapped.get("complete") is True or (not item["entity"] and entry.get("answer_status") == "answered"):
            # This is candidate progress until the immutable final audit passes.
            proven = (not item["entity"] and entry.get("status") == "satisfied") or any(
                c.get("requirement_id") == item["requirement_id"] and c.get("entity") == item["entity"] and c.get("facet") == item["facet"]
                for c in coverage.get("confirmed_cells", []))
            row.answer_status = "confirmed" if final_verified and proven else "candidate"
            row.acquisition_status = "qualified"
            row.reason_code = "" if row.answer_status == "confirmed" else "final_answer_pending"
            row.evidence_refs_json = _json({"answer_sha256": digest, "marker_starts": mapped.get("marker_starts") or [],
                "report_revision_id": coverage.get("report_revision_id"),
                "claim_occurrence_ids": entry.get("claim_occurrence_ids") or next((c.get("claim_occurrence_ids", [])
                    for c in coverage.get("confirmed_cells", []) if c.get("requirement_id") == item["requirement_id"]
                    and c.get("entity") == item["entity"] and c.get("facet") == item["facet"]), []),
                "decision_audit": entry.get("decision_audit") or coverage.get("decision_audit")})
        elif applicable:
            row.answer_status = "unanswered"
            if row.reason_code in {"", "final_answer_pending"}:
                row.reason_code = "coverage_mapping_missing"
            # Preserve prior specific cause and target for a malformed judgment.
        elif entries and row.answer_status in {"confirmed", "candidate"}:
            row.answer_status = "unanswered"
            row.reason_code = "coverage_mapping_missing"
        row.state_version += 1
    db.flush()


def begin_action(db, root, item, kind, arguments, evidence_revision):
    """Reserve before tool execution; no duplicate replay of an uncertain call."""
    args_hash = identity("", arguments)
    logical = identity("action-", item["work_item_id"], kind, args_hash, evidence_revision)
    prior = db.scalar(select(ResearchOperation).where(ResearchOperation.root_run_id == root,
        ResearchOperation.logical_key == logical).order_by(ResearchOperation.attempt.desc()))
    if prior is not None and prior.status != "deferred":
        return None
    attempt = prior.attempt + 1 if prior else 1
    operation = ResearchOperation(operation_id=identity("op-", root, logical, attempt), root_run_id=root,
        run_id=root, operation_kind=kind, logical_key=logical, attempt=attempt, status="running",
        arguments_hash=args_hash, started_at=datetime.now(timezone.utc), payload_json=_json({
            "work_item_id": item["work_item_id"], "requirement_id": item["requirement_id"],
            "entity": item["entity"], "facet": item["facet"], "cause": item["reason_code"],
            "state_version": item["state_version"], "arguments": arguments,
            "evidence_revision": evidence_revision, "expected_effect": "obtain evidence for this cell; rejudge before confirmation"}))
    db.add(operation)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if db.scalar(select(ResearchOperation).where(ResearchOperation.root_run_id == root, ResearchOperation.logical_key == logical)) is not None:
            return None
        raise
    return operation


def finish_action(db, operation, status, trace_id, effect):
    payload = json.loads(operation.payload_json)
    payload.update({"trace_id": trace_id or payload.get("trace_id"), "effect": effect})
    operation.payload_json = _json(payload)
    operation.status = status
    operation.finished_at = datetime.now(timezone.utc)
    row = db.get(ResearchWorkItem, payload["work_item_id"])
    if row is not None and status == "succeeded":
        row.acquisition_status = "acquired"
        row.state_version += 1
    db.commit()
