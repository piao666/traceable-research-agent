"""Serial, bounded evidence repair through the existing registry and root budget."""
from __future__ import annotations

from time import perf_counter
from typing import Any
import hashlib
import json
import re

from app.agent.budget import acquisition_budget, FinalizationRequired, current_budget
from app.agent.outcome import load_observations
from app.agent.source_context import build_source_context, resolve_source_snapshot
from app.agent.source_intake import execute_governed_operation, prepare_tool_arguments
from app.evidence.service import materialize_execution_provenance
from app.tools.registry import execute_tool
from app.trace import store
from app.trace.logger import record_tool_result


def refresh_comparison_contract(db: Any, run_id: str, plan: dict[str, Any], bundle: dict[str, Any], client: Any) -> bool:
    from app.research.comparison_scope import select_comparison_candidates
    from app.research.assessor import persist_plan_contract
    from app.agent.budget import budget_client
    contract = plan.get("task_contract") or {}
    if not contract.get("obligation_version"):
        return False
    # The caller owns the phase budget. A nested finalization context would
    # let ordinary selection judgments spend the protected writer reserve.
    selected = select_comparison_candidates(contract, bundle, budget_client(client))
    if selected == contract:
        return False
    plan["task_contract"] = selected
    persist_plan_contract(db, root_run_id=run_id, contract=selected)
    store.replace_agent_run_plan(db, run_id, plan)
    return True


def acquire_comparison_evidence(db: Any, run_id: str, plan: dict[str, Any], settings: Any,
                                bundle: dict[str, Any], *, traces: list[Any], bundle_loader: Any = None) -> dict[str, Any]:
    initial = acquisition_quality_gaps(plan.get("task_contract") or {}, bundle)
    rounds = min(8, len(initial) * max(0, min(4, settings.max_refetch_rounds)))
    for _ in range(rounds):
        gaps = acquisition_quality_gaps(plan.get("task_contract") or {}, bundle)
        if not gaps:
            break
        recover_answer_evidence(db, run_id, plan, settings, {"answer_gaps": gaps}, traces=traces)
        if (plan.get("answer_recovery") or {}).get("stop_reason") in {
            "same_gap_repair_round_limit", "protected_finalization_reserve", "rewrite_from_retained_context"}:
            break
        bundle = (bundle_loader() if bundle_loader else materialize_execution_provenance(db,
            store.get_fresh_agent_run(db, run_id), plan, load_observations(traces), traces, settings)) or bundle
    plan["acquisition_quality"] = {"version": "comparison-acquisition-v1", "gaps": acquisition_quality_gaps(
        plan.get("task_contract") or {}, bundle)}
    store.replace_agent_run_plan(db, run_id, plan)
    return bundle


def _gap_identity(gap: dict[str, Any]) -> tuple[str, str, str, str]:
    # Different reviewers can describe one missing application differently.
    # That wording must not create another acquisition allowance.
    gap = {**gap, "cause": "answer_content_missing" if gap.get("cause") == "application_workload_missing" else gap.get("cause")}
    return tuple(str(gap.get(k) or "") for k in ("requirement_id", "cause", "entity", "facet"))


def _body_units(traces: list[Any]) -> set[tuple[str, str]]:
    """Acquired body content, independent of changing trace/marker IDs."""
    units = set()
    for trace in traces:
        if trace.status != "success" or trace.tool_name not in {"web_fetcher", "pdf_reader", "file_reader"}:
            continue
        try:
            output = json.loads(trace.output_json or "{}")
        except (ValueError, TypeError):
            continue
        rows = list(output.get("pages") or [])
        if isinstance(output.get("source_content"), dict):
            rows.append(output["source_content"])
        for row in rows:
            text = str(row.get("content") or row.get("text") or "")
            if text.strip() and not row.get("error"):
                units.add((str(row.get("url") or row.get("source_url") or row.get("source_id") or ""),
                           text))
    return units


def acquisition_quality_gaps(contract: dict[str, Any], bundle: dict[str, Any]) -> list[dict[str, Any]]:
    """Acquire qualifying sources before spending on a comparison report.

    This checks source availability only; it never grants answer coverage.
    The final gate still checks the sources mapped to EACH actual claim.
    """
    from app.agent.evidence_requirements import assess_required_evidence
    from app.research.answer_coverage import _coverage_constraints
    from app.research.assessor import _source_independence_key
    if not contract.get("obligation_version") or not contract.get("comparison_scope"):
        return []
    spec = contract["comparison_scope"]
    if spec.get("selection_required") and not spec.get("entities"):
        from app.research.comparison_scope import selection_requirement_ids
        return [{"requirement_id": selection_requirement_ids(contract)[0], "cause": "comparison_selection_missing",
                 "predicate": contract.get("original_task"), "detail": "Acquire dated category and selection evidence."}]
    reqs = [r for r in contract.get("requirements", []) if r.get("required", True)]
    if len(reqs) > 1:
        reqs = [r for r in reqs if r["requirement_id"] != "req-original"]
    constraints = _coverage_constraints(contract, reqs)
    eligible = set(assess_required_evidence(contract, bundle).eligible_passage_ids)
    documents = {d["document_id"]: d for d in bundle.get("source_documents", [])}
    snapshots = {s["snapshot_id"]: s for s in bundle.get("source_snapshots", [])}
    gaps = []
    for r in reqs:
        for cell in constraints["cells"][r["requirement_id"]]:
            if cell["facet"] not in {"mechanism", "framework", "memory"}:
                continue
            primary, groups = False, set()
            for p in bundle.get("passages", []):
                if p.get("passage_id") not in eligible or not re.search(
                    r"(?<![A-Za-z0-9_])" + re.escape(cell["entity"]) + r"(?![A-Za-z0-9_])", str(p.get("text") or ""), re.I):
                    continue
                snapshot = snapshots.get(p.get("snapshot_id"), {})
                doc = documents.get(snapshot.get("document_id"), {})
                meta = {**doc.get("metadata", {}), **snapshot.get("metadata", {})}
                primary |= (meta.get("official") is True or meta.get("source_class") in {"official", "official_code", "regulatory"})
                group = _source_independence_key({"source_identity": meta.get("source_identity"), "url": doc.get("canonical_uri")})
                if group:
                    groups.add(group)
            if not primary and len(groups) < 2:
                gaps.append({"requirement_id": r["requirement_id"], **cell, "cause": "source_quality_missing",
                    "predicate": r["predicate"], "detail": "Acquire primary implementation evidence or independent corroboration."})
    return gaps


def recover_answer_evidence(db: Any, run_id: str, plan: dict[str, Any], settings: Any,
                            feedback: dict[str, Any], *, traces: list[Any] | None = None) -> bool:
    if not (plan.get("task_contract") or {}).get("obligation_version"):
        return False
    if store.is_agent_run_cancelled(db, run_id):
        return False
    from app.research.control_store import sync_work, apply_coverage, open_work, ensure_work, project_work, begin_action, finish_action
    sync_work(db, run_id, plan)
    gaps = feedback.get("answer_gaps") or []
    if gaps:
        apply_coverage(db, run_id, {"gaps": gaps, "requirements": []})
        if all(g.get("cause") == "coverage_mapping_missing" for g in gaps):
            concrete = [w for w in open_work(db, run_id) if w["entity"] and w["reason_code"] != "coverage_mapping_missing"]
            if concrete:
                gaps = [{"requirement_id": w["requirement_id"], "entity": w["entity"], "facet": w["facet"],
                    "cause": w["reason_code"], "detail": w["detail"]} for w in concrete]
    unsupported = feedback.get("must_remove_or_rewrite_unsupported") or []
    if gaps and all(g.get("cause") == "inventory_identity_missing" for g in gaps):
        # Correct the answer against retained identity context. Fetching a
        # namesake cannot repair a wrong original inventory binding.
        plan.setdefault("answer_recovery", {"version": "answer-recovery-v3", "attempts": []})["stop_reason"] = "repair_from_retained_windows"
        store.replace_agent_run_plan(db, run_id, plan)
        return False
    if not gaps and not unsupported:
        return False
    state = plan.setdefault("answer_recovery", {"version": "answer-recovery-v3", "attempts": []})
    state["version"] = "answer-recovery-v3"
    attempts = state["attempts"]
    local_reasons = {"non_substantive_quote", "missing_source_condition", "quote_not_in_frozen_window",
                     "duplicate_provider_verdict_identity", "explicit_statement_quote_contradiction"}
    if not gaps and unsupported and all(item.get("application_reason") in local_reasons for item in unsupported):
        state["stop_reason"] = "repair_from_retained_windows"
        store.replace_agent_run_plan(db, run_id, plan)
        return False
    # New coverage gaps have their own allowance. Failed optional citations
    # cannot consume the only two acquisition opportunities for a later gap.
    targets = actionable_gaps(gaps) or ([] if gaps else [{"requirement_id": "citation", "cause": u.get("application_reason"),
        "entity": re.sub(r"CIT-\d{3}-\d{2}", "", str(u.get("sentence") or ""))} for u in unsupported]
    )
    if not targets:
        state["stop_reason"] = "repair_from_retained_windows"
        store.replace_agent_run_plan(db, run_id, plan)
        return False
    # Selection is one global prerequisite, regardless of how many technical
    # obligations inherit it. Do not rotate identical searches across r1/r2/….
    from app.research.comparison_scope import selection_requirement_ids
    targets = [{**g, "requirement_id": selection_requirement_ids(plan["task_contract"])[0], "entity": "", "facet": "selection"}
               if g.get("cause") == "comparison_selection_missing" else g for g in targets]
    if any(g.get("cause") == "comparison_selection_missing" for g in targets):
        rejections = ((plan["task_contract"].get("comparison_scope") or {}).get("selection_attempt") or {}).get("rejections") or []
        reasons = {"category_identity_missing": "identity", "category_quote_missing": "category",
            "selection_quote_missing": "criterion", "selection_criterion_missing": "criterion",
            "dated_activity_missing": "date", "entity_version_unverified": "version"}
        named = [{"requirement_id": selection_requirement_ids(plan["task_contract"])[0],
                  "entity": r.get("canonical_name") or r.get("name"), "facet": "selection_" + reasons[r["cause"]],
                  "cause": r["cause"], "detail": "Resolve this candidate's selection predicate before freezing the cohort."}
                 for r in rejections if r.get("cause") in reasons and (r.get("canonical_name") or r.get("name"))]
        if named:
            targets = named
    unique = {_gap_identity(g): g for g in targets}
    def target_key(g):
        return hashlib.sha256(json.dumps(_gap_identity(g), ensure_ascii=False).encode()).hexdigest()
    max_rounds = min(4, max(0, int(settings.max_refetch_rounds)))
    def attempt_count(g):
        return sum(_gap_identity(dict(zip(("requirement_id", "cause", "entity", "facet"), a.get("target", [])))) == _gap_identity(g)
                   for a in attempts)
    target = min(unique.values(), key=attempt_count)
    work_row = ensure_work(db, run_id, target.get("requirement_id") or "citation", target.get("entity") or "", target.get("facet") or "answer")
    if str(target.get("facet") or "").startswith("selection_"):
        work_row.reason_code = str(target.get("cause") or "comparison_selection_missing")
        work_row.detail = str(target.get("detail") or "")
        db.flush()
    work = next(w for w in project_work(db, run_id) if w["work_item_id"] == work_row.work_item_id)
    # Reproject already admitted material first. This changes the writer's
    # exact window, not evidence or verdicts, and uses no acquisition round.
    selection_target = str(target.get("facet") or "").startswith("selection")
    if target.get("entity") and target.get("cause") not in {"source_quality_missing", "comparison_selection_missing"} and not selection_target:
        focus = {k: target.get(k) for k in ("requirement_id", "entity", "facet")}
        prior_focus = plan["task_contract"].setdefault("evidence_focus", [])
        if not any(all(f.get(k) == focus.get(k) for k in ("requirement_id", "entity", "facet")) for f in prior_focus):
            operation = begin_action(db, run_id, work, "reproject", focus, "admitted-context-v1")
            if operation:
                prior_focus.append(focus)
                finish_action(db, operation, "succeeded", None, {"new_body_units": 0, "requires_rejudgment": True})
                project_work(db, run_id, plan)
                state["stop_reason"] = "reproject_retained_context"
                store.replace_agent_run_plan(db, run_id, plan)
                return True
    repair_key = target_key(target)
    matching_attempts = attempt_count(target)
    if matching_attempts >= max_rounds:
        state["stop_reason"] = "same_gap_repair_round_limit"
        store.replace_agent_run_plan(db, run_id, plan)
        return False
    runtime = current_budget()
    if runtime is not None and not runtime.can_deepen(required_llm_calls=0):
        state["stop_reason"] = "protected_finalization_reserve"
        store.replace_agent_run_plan(db, run_id, plan)
        return False
    attempted_urls = {url for attempt in attempts for action in attempt.get("actions", [])
                      for url in action.get("arguments", {}).get("urls", [])}
    traces = traces or store.list_tool_traces(db, run_id)
    sources = build_source_context(traces)["sources"]
    fetched_urls = {s["url"] for s in sources if s.get("fetch_status") == "fetched"}
    actions: list[tuple[str, dict[str, Any]]] = []
    allowed = set(plan.get("allowed_tools") or [])
    hints = (plan.get("task_contract") or {}).get("research_terms") or {}
    cause = target.get("cause", "answer_content_missing")
    if cause == "coverage_mapping_missing":
        # An unbound inventory is a research prerequisite. Search the literal
        # obligation if no earlier cell can be recovered from the ledger.
        cause = "answer_content_missing"
    query = " ".join(str(value or "") for value in (target.get("entity"), target.get("facet"),
        hints.get(target.get("requirement_id")) or target.get("predicate")))
    if target.get("facet") == "application":
        query += " concrete use case examples business workflow"
    if cause == "comparison_selection_missing":
        from app.research.comparison_scope import selection_query
        branches = int((plan.get("task_contract") or {}).get("comparison_scope", {}).get("selection_search_rounds", 0))
        query = selection_query(plan["task_contract"], branches + matching_attempts)
    elif selection_target:
        selection_focus = {"selection_identity": "official product name personal autonomous assistant",
            "selection_category": "personal autonomous assistant product documentation",
            "selection_criterion": "dated comparative review adoption activity",
            "selection_date": "review publication date release activity",
            "selection_version": "official release version"}.get(target.get("facet"), "comparative review")
        from app.research.comparison_scope import selection_year
        query = f"{target.get('entity')} {selection_year(plan['task_contract'])} {selection_focus}"
    if not query:
        query = str((plan.get("task_contract") or {}).get("original_task") or plan.get("task") or "")
        query += " " + " ".join(str(u.get("sentence") or "") for u in unsupported[:2])
    if cause != "comparison_selection_missing" and not selection_target and (plan.get("task_contract") or {}).get("comparison_scope"):
        query += " official documentation implementation source code"
    if matching_attempts and cause != "comparison_selection_missing":
        query += " independent primary source"
    # Read a relevant retained continuation before acquiring more URLs. The
    # immutable snapshot carries the original trace and full-body hash.
    from app.agent.evidence_requirements import _task_terms
    terms = _task_terms(query)
    attempted_reads = {(a.get("arguments", {}).get("source_id"), a.get("arguments", {}).get("offset"))
                       for attempt in attempts for a in attempt.get("actions", []) if a.get("arguments", {}).get("source_id")}
    if "web_fetcher" in allowed and cause not in {"source_quality_missing", "comparison_selection_missing"} and not selection_target and not matching_attempts:
        ranked = []
        for source in sources:
            if source.get("fetch_status") != "fetched" or not source.get("source_id"):
                continue
            snapshot = resolve_source_snapshot(traces, source["source_id"],
                origin_trace_id=source.get("origin_trace_id"), source_content_sha256=source.get("content_hash"))
            if snapshot is None:
                continue
            lower = snapshot.text.casefold()
            visible = int(source.get("view_chars") or len(snapshot.text))
            for term in terms:
                offset = lower.find(term.casefold())
                if offset < 0:
                    continue
                start = max(0, offset - 1800)
                if (source["source_id"], start) in attempted_reads:
                    continue
                window = lower[start:start + 6000]
                ranked.append((sum(t.casefold() in window for t in terms), source, start))
        seen = set()
        for _, source, start in sorted(ranked, key=lambda item: item[0], reverse=True):
            if source["source_id"] in seen:
                continue
            seen.add(source["source_id"])
            actions.append(("web_fetcher", {"source_id": source["source_id"], "offset": start, "max_chars": 6000,
                "origin_trace_id": source.get("origin_trace_id"), "source_content_sha256": source.get("content_hash")}))
            if len(actions) == 2:
                break
    if not actions and "tavily_search" in allowed and query.strip():
        actions.append(("tavily_search", {"query": query[:1500], "max_results": 4}))
    elif not actions and "web_fetcher" in allowed:
        pending = [s["url"] for s in sources if s["fetch_status"] != "fetched" and s["url"] not in attempted_urls
                   and any(term in (str(s.get("title") or "") + " " + s["url"]).casefold() for term in terms)]
        pending = prepare_tool_arguments("web_fetcher", {"urls": pending}, plan, settings).get("urls", [])
        if pending:
            actions.append(("web_fetcher", {"urls": pending[:2], "max_chars": 50000}))
    if not actions:
        state["stop_reason"] = "rewrite_from_retained_context"
        store.replace_agent_run_plan(db, run_id, plan)
        return False
    attempt = {"round": len(attempts) + 1, "repair_key": repair_key, "target": _gap_identity(target), "gaps": gaps, "actions": []}
    attempts.append(attempt)
    store.replace_agent_run_plan(db, run_id, plan)
    before = _body_units(traces)
    protected = False
    try:
        with acquisition_budget():
            while actions and len(attempt["actions"]) < 2:
                if store.is_agent_run_cancelled(db, run_id):
                    break
                name, arguments = actions.pop(0)
                evidence_revision = hashlib.sha256(json.dumps(sorted(before), ensure_ascii=False).encode()).hexdigest()
                operation = begin_action(db, run_id, work, "read_retained" if arguments.get("source_id") else name, arguments, evidence_revision)
                if operation is None:
                    continue
                started = perf_counter()
                try:
                    result = execute_governed_operation(name, arguments, plan, settings, execute_tool)
                except BaseException as exc:
                    # A possibly issued call is not automatically replayed on
                    # restart. Keep its intent for explicit reconciliation.
                    from app.agent.budget import BudgetExceeded
                    operation.status = "deferred" if isinstance(exc, BudgetExceeded) else "interrupted"
                    db.commit()
                    raise
                trace = record_tool_result(db, run_id, max((t.step_no for t in traces), default=0) + 1,
                    name, arguments, result, int((perf_counter() - started) * 1000))
                traces.append(trace)
                finish_action(db, operation, "succeeded" if result.success else "failed", trace.trace_id,
                    {"new_body_units": len(_body_units(traces) - before), "requires_rejudgment": True})
                attempt["actions"].append({"tool_name": name, "trace_id": trace.trace_id,
                    "status": "success" if result.success else "failed", "arguments": arguments, "operation_id": operation.operation_id})
                if result.success and name in {"web_fetcher", "pdf_reader", "file_reader"} and target.get("entity"):
                    focus = next((f for f in plan["task_contract"].setdefault("evidence_focus", [])
                        if all(f.get(k) == target.get(k) for k in ("requirement_id", "entity", "facet"))), None)
                    if focus is None:
                        focus = {k: target.get(k) for k in ("requirement_id", "entity", "facet")}
                        plan["task_contract"]["evidence_focus"].append(focus)
                    if trace.trace_id not in focus.setdefault("trace_ids", []):
                        focus["trace_ids"].append(trace.trace_id)
                store.update_agent_run_progress(db, run_id, trace.step_no,
                    total_tool_calls_delta=0 if result.metadata.get("executed") is False else 1)
                store.replace_agent_run_plan(db, run_id, plan)
                if name == "tavily_search" and result.success and "web_fetcher" in allowed:
                    urls = (result.output or {}).get("fetch_candidates") or []
                    if cause == "comparison_selection_missing":
                        from app.research.comparison_scope import relevant_selection_urls
                        urls = relevant_selection_urls(result.output or {}, urls, plan["task_contract"])
                    urls = prepare_tool_arguments("web_fetcher", {"urls": [u for u in urls if u not in attempted_urls | fetched_urls]}, plan, settings).get("urls", [])
                    if urls:
                        actions.append(("web_fetcher", {"urls": urls[:2], "max_chars": 50000}))
    except FinalizationRequired:
        protected = True
    run = store.get_fresh_agent_run(db, run_id)
    materialize_execution_provenance(db, run, plan, load_observations(traces), traces, settings)
    added = {unit for unit in _body_units(traces) - before
             if not any(unit[0] == old[0] and unit[1] in old[1] for old in before)}
    attempt["new_body_units"] = len(added)
    attempt["evidence_changed"] = bool(added)
    project_work(db, run_id, plan)
    state["stop_reason"] = "protected_finalization_reserve" if protected else (None if added else "no_new_body_evidence")
    store.replace_agent_run_plan(db, run_id, plan)
    return bool(added)


def actionable_gaps(gaps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate coverage reuses individual cells; mapping is a local repair."""
    concrete = [g for g in gaps if g.get("cause") != "inventory_identity_missing"]
    individual = {(g.get("entity"), g.get("facet")) for g in concrete if g.get("requirement_id") != "req-original"}
    return [g for g in concrete if g.get("requirement_id") != "req-original" or
            (g.get("entity"), g.get("facet")) not in individual]
