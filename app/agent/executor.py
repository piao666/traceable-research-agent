"""Manual executor for deterministic plans."""

from __future__ import annotations

import json
import hashlib
import logging
from time import perf_counter
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from app.agent.file_access_policy import file_reader_execution_arguments
from app.agent.preflight import enforce_execution_readiness
from app.agent.outcome import dependency_missing, enforce_research_outcome, fail_execution, finalize_terminal_decision, load_observations, report_subject, result_integrity, skip_dependency
from app.agent.budget import budgeted_execution
from app.agent.report_generation import ReportGenerationAudit, check_report_generation_not_cancelled, resolve_report_llm_client
from app.agent.reporter import generate_markdown_report, render_discovery_report, save_report
from app.agent.source_intake import execute_governed_operation, prepare_tool_arguments, research_profile
from app.agent.evidence_requirements import assess_required_evidence
from app.config import Settings, settings as _exec_settings
from app.evidence.service import materialize_execution_provenance
from app.evidence.citation_validator import materialize_final_report_occurrences, validator_version_for
from app.reporting.integrity import assess_report_integrity
from app.llm.base import LLMClient
from app.mcp.policy import MCPChannel, is_tool_read_only, requires_interactive_confirmation, tool_channel
from app.tools.base import ToolResult
from app.tools.registry import execute_tool, get_tool
from app.trace import store
from app.trace.logger import record_tool_result, record_trace_event
from app.trace.models import AgentRun


def _persist_citation_validation(
    db: Session,
    run_id: str,
    validation_reports: list[Any],
    traces: list[Any],
) -> AgentRun:
    """Persist and trace the exact validation result rendered in the report."""
    if not validation_reports:
        run = store.get_agent_run(db, run_id)
        if run is None:
            raise ValueError("Task run not found")
        return run
    validation = validation_reports[-1]
    multilingual = getattr(validation, "multilingual_adjudication", {}) or {}
    multilingual_token_in = int(multilingual.get("token_in") or 0) if isinstance(multilingual, dict) else 0
    multilingual_token_out = int(multilingual.get("token_out") or 0) if isinstance(multilingual, dict) else 0
    # A report-generation callback already records each actual multilingual
    # provider call as ``citation_adjudication``.  The final validator trace
    # must retain the verdict but exclude those tokens/costs to avoid charging
    # the same call twice; cached final validation has zero local usage.
    callback_accounted = bool(isinstance(multilingual, dict) and multilingual.get("usage_traced"))
    trace_token_in = max(0, int(validation.token_in) - (multilingual_token_in if callback_accounted else 0))
    trace_token_out = max(0, int(validation.token_out) - (multilingual_token_out if callback_accounted else 0))
    run = store.update_agent_run_citation_validation(
        db,
        run_id,
        total=validation.total,
        supported=validation.supported,
        weakly_supported=validation.weakly_supported,
        unsupported=validation.unsupported,
        accuracy=validation.accuracy,
    )
    if validation.total <= 0:
        return run

    estimated_cost = 0.0
    if validation.llm_used or (multilingual and not callback_accounted):
        try:
            from app.llm.cost import estimate_cost_from_tokens

            estimated_cost = estimate_cost_from_tokens(
                validation.llm_provider or str(multilingual.get("provider") or "unknown"),
                validation.llm_model or multilingual.get("model"),
                trace_token_in,
                trace_token_out,
            )
        except Exception:
            estimated_cost = 0.0
    record_trace_event(
        db=db,
        run_id=run_id,
        step_no=max((trace.step_no for trace in traces), default=0) + 1,
        tool_name="citation_validator",
        status="success",
        input_data={"total_citations": validation.total},
        output_summary=(
            f"Citation validation: {validation.supported}/{validation.total} supported "
            f"({validation.accuracy * 100:.1f}%), "
            f"{validation.weakly_supported} weak, {validation.unsupported} unsupported"
        ),
        output_data=validation.to_dict(),
        token_in=trace_token_in,
        token_out=trace_token_out,
        estimated_cost=estimated_cost,
    )
    return run


def _persist_reference_verification(
    db: Session,
    run_id: str,
    ref_reports: list[Any],
    traces: list[Any],
) -> AgentRun:
    """Persist and trace reference verification results from the report pipeline.

    Metrics are stored in the trace event output_data. Dedicated columns on
    AgentRun will be added by migration 0010 after the schema stabilizes.
    """
    run = store.get_agent_run(db, run_id)
    if run is None:
        raise ValueError("Task run not found")
    if not ref_reports:
        return run
    report = ref_reports[-1]
    if report.total <= 0:
        return run

    # Trace event (metrics stored here; migration 0010 adds AgentRun columns later)
    record_trace_event(
        db=db,
        run_id=run_id,
        step_no=max((trace.step_no for trace in traces), default=0) + 1,
        tool_name="reference_verifier",
        status="success",
        input_data={"total_references": report.total},
        output_summary=(
            f"Reference verification: {report.verified}/{report.total} verified, "
            f"{report.inconsistent} inconsistent, {report.unresolved} unresolved"
        ),
        output_data=report.to_dict(),
    )
    return run


def _persist_final_report_gate(
    db: Session,
    run: AgentRun,
    plan: dict[str, Any],
    markdown: str,
    provenance_bundle: dict[str, Any],
    report_path: str,
    validation_reports: list[Any],
) -> dict[str, Any]:
    """Materialize the final report revision before terminal status is chosen."""
    provenance_bundle = provenance_bundle if isinstance(provenance_bundle, dict) else {
        "passages": [], "citations": [], "source_documents": [], "integrity": {}
    }
    bundle = materialize_final_report_occurrences(
        db,
        root_run_id=run.run_id,
        markdown=markdown,
        provenance_bundle=provenance_bundle,
        report_path=report_path,
        validation_report=validation_reports[-1] if validation_reports else None,
    )
    integrity = assess_report_integrity(bundle, scope_bundle=provenance_bundle)
    quick_evidence_assessment = _quick_evidence_gate_failure(plan, provenance_bundle)
    if quick_evidence_assessment is not None:
        integrity_payload = integrity.to_plan_dict()
        integrity_payload.update({
            "status": "failed",
            "error_code": "quick_requires_full_text",
            "warnings": [
                *integrity_payload.get("warnings", []),
                "Quick substantive research requires task-eligible, traceable body evidence; discovery snippets are contextual only.",
            ],
            "evidence_assessment": quick_evidence_assessment.as_dict(),
        })
    else:
        integrity_payload = integrity.to_plan_dict()
    plan["report_integrity"] = integrity_payload
    plan["report_revision_id"] = bundle["report_revision"]["report_revision_id"]
    # Freeze the adopted revision and validator inputs in one public identity.
    # This is deliberately derived from persisted bytes and occurrence data,
    # never from a client-provided marker.
    revision = bundle["report_revision"]
    validation_payload = (
        validation_reports[-1].to_dict() if validation_reports and hasattr(validation_reports[-1], "to_dict")
        else validation_reports[-1] if validation_reports else {}
    )
    audit_manifest = plan.get("report_generation") if isinstance(plan.get("report_generation"), dict) else {}
    writing_manifest_hash = audit_manifest.get("manifest_sha256") or audit_manifest.get("manifest_hash")
    evidence_snapshot_id = hashlib.sha256(
        json.dumps(provenance_bundle, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    validator_version = validator_version_for(validation_reports[-1] if validation_reports else None)
    manifest_payload = {
        "version": "report-generation-v1",
        "report_revision_id": revision.get("report_revision_id"),
        "content_hash": revision.get("content_hash"),
        "final_answer_hash": revision.get("final_answer_hash"),
        "integrity": integrity_payload,
        "validation": validation_payload,
        "attempt_manifest": audit_manifest,
    }
    manifest_sha256 = hashlib.sha256(
        json.dumps(manifest_payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    validation_identity = hashlib.sha256(
        json.dumps({"content_hash": revision.get("content_hash"),
                    "evidence_snapshot_id": evidence_snapshot_id,
                    "writing_manifest_hash": writing_manifest_hash,
                    "validator_version": validator_version,
                    "validation": validation_payload},
                   ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    plan["report_sha256"] = revision.get("content_hash")
    plan["report_manifest_sha256"] = manifest_sha256
    plan["report_generation"] = {
        **manifest_payload,
        "manifest_sha256": manifest_sha256,
        "validation_identity": validation_identity,
        "evidence_snapshot_id": evidence_snapshot_id,
        "writing_manifest_hash": writing_manifest_hash,
        "validator_version": validator_version,
        # Adoption identifies the final displayed revision, not quality
        # success.  A failed/incomplete quality gate may still have a fully
        # validated final artifact whose citation evaluation must remain
        # visible; only an absent validator result is non-adopted.
        "adopted": bool(validation_reports),
    }
    if plan.get("quick_output_mode") == "discovery":
        # The discovery hash is a capability binding: only the deterministic
        # source/title/url renderer is eligible.  A marker in an arbitrary
        # report is insufficient because it could contain substantive text.
        traces = store.list_tool_traces(db, run.run_id)
        expected = render_discovery_report(
            report_subject(run), plan, load_observations(traces), traces,
        )
        if markdown == expected:
            plan["discovery_report_sha256"] = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
        else:
            plan.pop("discovery_report_sha256", None)
    store.replace_agent_run_plan(db, run.run_id, plan)
    return integrity_payload


def _quick_evidence_gate_failure(
    plan: dict[str, Any],
    provenance_bundle: dict[str, Any],
) -> Any | None:
    """Return the shared evidence failure for a substantive Quick report.

    A fetched ``partial`` passage remains partial.  It may nevertheless be a
    task-relevant, persisted body read and therefore satisfy the same A2
    evidence contract that governs execution.  This prevents the final-report
    gate from contradicting the earlier evidence assessment while retaining
    every role, provenance, source-constraint, relevance, and quality check.
    """
    if (
        plan.get("research_mode") != "quick"
        or plan.get("quick_output_mode") == "discovery"
    ):
        return None
    from app.agent.evidence_requirements import assess_required_evidence

    assessment = assess_required_evidence(plan.get("task_contract"), provenance_bundle)
    return None if assessment.passed else assessment


def _run_quick_refetches(
    db: Session,
    run: AgentRun,
    plan: dict[str, Any],
    settings_obj: Settings,
    observations: list[dict[str, Any]],
    traces: list[Any],
    provenance_bundle: dict[str, Any] | None,
    assessment: Any,
    *,
    report_feedback: bool = False,
) -> tuple[list[Any], dict[str, Any] | None, Any]:
    """Try bounded, undispatched discoveries when Quick body evidence is incomplete."""
    if (
        plan.get("research_mode") != "quick"
        or plan.get("quick_output_mode") == "discovery"
        or assessment is None
        or (assessment.passed and not report_feedback)
    ):
        return traces, provenance_bundle, assessment

    from app.agent.quick_refetch import select_pending_candidates

    profile = research_profile(plan, settings_obj)
    max_candidates = max(0, int(getattr(settings_obj, "max_fetch_candidates", 0)))
    max_rounds = min(
        max(0, int(getattr(settings_obj, "max_refetch_rounds", 0))),
        max(0, int(profile.max_recovery_rounds)),
    )
    state = plan.setdefault("quick_refetch", {
        "version": "quick-refetch-v1",
        "status": "incomplete",
        "max_candidates": max_candidates,
        "max_rounds": max_rounds,
        "attempts": [],
    })
    if not isinstance(state, dict):
        state = plan["quick_refetch"] = {
            "version": "quick-refetch-v1", "status": "incomplete",
            "max_candidates": max_candidates, "max_rounds": max_rounds, "attempts": [],
        }
    attempts = state.setdefault("attempts", [])
    if not isinstance(attempts, list):
        attempts = state["attempts"] = []

    used_rounds = min(max_rounds, len(attempts))
    if used_rounds >= max_rounds:
        state["status"] = "round_limit_reached"
    last_round = min(max_rounds, used_rounds + 1) if report_feedback else max_rounds
    for round_no in range(used_rounds + 1, last_round + 1):
        if (assessment.passed and not report_feedback) or store.is_agent_run_cancelled(db, run.run_id):
            break
        candidates = select_pending_candidates(
            traces,
            max_total_candidates=max_candidates,
            remaining_rounds=max_rounds - round_no + 1,
        )
        if not candidates:
            state["status"] = "exhausted"
            break
        arguments = prepare_tool_arguments(
            "web_fetcher", {"urls": candidates}, plan, settings_obj
        )
        urls = arguments.get("urls") if isinstance(arguments.get("urls"), list) else []
        if not urls:
            state["status"] = "no_eligible_candidates"
            break

        step_no = max(
            [int(getattr(trace, "step_no", 0) or 0) for trace in traces]
            + [int(step.get("step_no") or 0) for step in plan.get("steps") or [] if isinstance(step, dict)]
            + [0]
        ) + 1
        started = perf_counter()
        result = execute_governed_operation(
            "web_fetcher",
            arguments,
            plan,
            settings_obj,
            execute_tool,
            arguments_prepared=True,
        )
        latency_ms = int((perf_counter() - started) * 1000)
        trace = record_tool_result(
            db, run.run_id, step_no, "web_fetcher", arguments, result, latency_ms
        )
        observations.append({
            "trace_id": trace.trace_id,
            "step_no": step_no,
            "tool_name": "web_fetcher",
            "success": result.success,
            "output_summary": result.output_summary,
            "error_message": result.error_message,
            "output": result.output,
            "metadata": result.metadata,
        })
        attempts.append({
            "trigger": "report_evidence_gap" if report_feedback else "body_evidence_gap",
            "round": round_no,
            "step_no": step_no,
            "urls": list(urls),
            "status": "success" if result.success else "failed",
            "trace_id": trace.trace_id,
        })
        run = store.update_agent_run_progress(
            db,
            run.run_id,
            step_no,
            total_tool_calls_delta=0 if (result.metadata or {}).get("executed") is False else 1,
            latency_ms_delta=latency_ms,
        )
        # Persist the attempt before rematerializing so crashes retain a
        # reviewable record even when the new evidence revision is incomplete.
        store.replace_agent_run_plan(db, run.run_id, plan)
        traces = store.list_tool_traces(db, run.run_id)
        provenance_bundle = materialize_execution_provenance(
            db, run, plan, observations, traces, settings_obj
        )
        assessment = assess_required_evidence(
            plan.get("task_contract"), provenance_bundle
        )
        plan["evidence_assessment"] = assessment.as_dict()
        state["status"] = "passed" if assessment.passed else "incomplete"
        state["remaining_rounds"] = max_rounds - round_no
        store.replace_agent_run_plan(db, run.run_id, plan)

    return traces, provenance_bundle, assessment


def _after_run_completed(
    db: Session,
    run: AgentRun,
    markdown: str,
    step_no: int,
) -> None:
    """Post-completion hooks: ChatTurn creation + memory extraction.

    Called after report generation succeeds, before status is set to completed.
    """
    # ── Create ChatTurn ─────────────────────────────────────────────
    if run.session_id:
        try:
            from app.memory.store import create_chat_turn

            summary = markdown[:500].replace("\n", " ").strip()
            create_chat_turn(
                db,
                run.session_id,
                "agent",
                summary or run.task,
                run_id=run.run_id,
            )
        except Exception:
            pass  # ChatTurn failure must not block run completion

    # ── Memory extraction ───────────────────────────────────────────
    try:
        from app.memory.extractor import (
            commit_pending_memories,
            extract_preferences_from_run,
            extract_preferences_with_llm,
            should_extract_for_run,
        )

        if should_extract_for_run(db):
            # Rule-based extraction (always runs)
            candidates = extract_preferences_from_run(db, run)

            # LLM-based extraction (optional, Phase 5)
            if _exec_settings.memory_llm_extraction_enabled:
                try:
                    from app.llm.providers import create_llm_client
                    llm = create_llm_client(_exec_settings)
                    llm_candidates = extract_preferences_with_llm(
                        run, [], llm,
                    )
                    candidates.extend(llm_candidates)
                except Exception:
                    pass  # LLM extraction failure → continue with rule-only

            new_count = commit_pending_memories(db, run, candidates)
            if new_count > 0:
                record_trace_event(
                    db=db,
                    run_id=run.run_id,
                    step_no=step_no,
                    tool_name="memory_extraction",
                    status="success",
                    input_data={},
                    output_summary=f"Extracted {new_count} new pending memories",
                    output_data={"new_pending": new_count},
                )
    except Exception:
        pass  # Extraction failure must not block run completion


EXECUTABLE_TOOLS = {
    "file_reader",
    "sql_query",
    "mcp_github_search",
    "tavily_search",
    "memory_search",
    "web_fetcher",
    "pdf_reader",
    "arxiv_search",
    "semantic_scholar_search",
    "openalex_search",
    "crossref_search",
}


def is_executable_tool(tool_name: str) -> bool:
    """Return whether a tool can be executed by the structured executor.

    Enforces the explicit executable allowlist, the WRITE-channel boundary,
    and the read-only guarantee (structural, not just a tag convention).
    """

    if tool_name == "report_writer":
        return False
    spec = get_tool(tool_name)
    if spec is None:
        return False
    is_remote_mcp = (
        (spec.metadata or {}).get("tool_source") == "mcp_remote"
        or "mcp_remote" in spec.tags
    )
    # Built-in tools keep an explicit allowlist.  Remote MCP tools are
    # discovered dynamically, so their registry policy metadata is the
    # executable boundary instead of a name that cannot be known in advance.
    if tool_name not in EXECUTABLE_TOOLS and not is_remote_mcp:
        return False
    return bool(
        spec.enabled
        and tool_channel(spec) != MCPChannel.WRITE.value
        and is_tool_read_only(spec)
    )


def _step_requires_confirmation(step: dict[str, Any], tool_name: str) -> bool:
    spec = get_tool(tool_name)
    return bool(step.get("requires_confirmation")) or requires_interactive_confirmation(spec)


def _parse_plan(run: AgentRun) -> dict[str, Any]:
    if not run.plan_json:
        raise ValueError("Task run does not have a plan_json.")
    return json.loads(run.plan_json)


def _summary(run: AgentRun) -> dict[str, Any]:
    plan: dict[str, Any] = {}
    if run.plan_json:
        try:
            parsed = json.loads(run.plan_json)
            if isinstance(parsed, dict):
                plan = parsed
        except json.JSONDecodeError:
            pass
    react_state = plan.get("react_state")
    if not isinstance(react_state, dict):
        react_state = {}
    return {
        **result_integrity(run),
        "run_id": run.run_id,
        "status": run.status,
        "current_step": run.current_step,
        "total_steps": run.total_steps,
        "total_tool_calls": run.total_tool_calls,
        "report_url": f"/api/reports/{run.run_id}",
        "trace_url": f"/api/tasks/{run.run_id}/trace",
        "error_message": run.error_message,
        "message": None,
        "execution_mode": plan.get("execution_mode") or "planned",
        "planner_source": plan.get("planner_source"),
        "llm_provider": react_state.get("llm_provider") or plan.get("llm_provider"),
        "llm_model": react_state.get("llm_model") or plan.get("llm_model"),
    }


def _resolve_arguments_from(
    step: dict[str, Any],
    observations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Resolve `arguments_from` references from previous step outputs.

    Supported syntax:
        arguments_from: {"step_no": 1, "field": "results"}
        → extracts step_results[1].output["results"]

    For tavily_search results (list of dict with "url" key), auto-extracts URLs.
    """
    args_from = step.get("arguments_from")
    if not isinstance(args_from, dict):
        return step.get("arguments") or {}

    source_step_no = args_from.get("step_no")
    field = args_from.get("field")

    if source_step_no is None or not field:
        return step.get("arguments") or {}

    # Find the observation from the referenced step
    source_observations = [
        obs for obs in observations if obs.get("step_no") == source_step_no
    ]
    if not source_observations:
        return step.get("arguments") or {}

    resolved_values = [
        output.get(field)
        for obs in source_observations
        if isinstance((output := obs.get("output")), dict)
        and output.get(field) is not None
    ]
    resolved_value = resolved_values[0] if resolved_values else None

    # For tavily_search results → extract URLs
    if field in {"results", "papers"} and resolved_values:
        urls: list[str] = []
        for value in resolved_values:
            if not isinstance(value, list):
                continue
            for item in value:
                if not isinstance(item, dict):
                    continue
                url = next(
                    (
                        str(item[key])
                        for key in ("url", "abstract_url", "openAccessUrl", "pdf_url", "id")
                        if str(item.get(key) or "").startswith(("http://", "https://"))
                    ),
                    "",
                )
                if url and url not in urls:
                    urls.append(url)
        if urls:
            merged = dict(step.get("arguments") or {})
            merged["urls"] = urls
            return merged

    # Generic field extraction
    if resolved_value is not None:
        merged = dict(step.get("arguments") or {})
        merged[field] = resolved_value
        return merged

    return step.get("arguments") or {}


def _failed_observation(step: dict[str, Any], result: ToolResult) -> dict[str, Any]:
    return {
        "step_no": step.get("step_no"),
        "tool_name": step.get("tool_name"),
        "success": result.success,
        "output_summary": result.output_summary,
        "error_message": result.error_message,
        "output": result.output,
        "metadata": result.metadata,
    }


def _is_step_confirmed(plan: dict[str, Any], step_no: int) -> bool:
    confirmation = plan.get("confirmation")
    if not isinstance(confirmation, dict):
        return False
    return bool(confirmation.get("approved")) and confirmation.get("required_step_no") == step_no


def _message_summary(run: AgentRun, message: str) -> dict[str, Any]:
    summary = _summary(run)
    summary["message"] = message
    return summary


@budgeted_execution
def run_plan(
    db: Session,
    run_id: str,
    settings_obj: Settings = _exec_settings,
    report_llm_client: LLMClient | None = None,
    completion_status: str = "completed",
) -> dict[str, Any]:
    """Execute a run plan step by step and generate a Markdown report."""

    run = store.get_agent_run(db, run_id)
    if run is None:
        raise ValueError("Task run not found.")
    if run.status == "completed":
        return _message_summary(run, "Run already completed; no tools executed.")
    if run.status in ("failed", "cancelled"):
        return _message_summary(run, f"Run is {run.status} and cannot be executed.")
    if run.status in {"waiting_human", "waiting_human_plan"}:
        return _message_summary(run, "Run is waiting for human approval.")

    plan = _parse_plan(run)
    if not enforce_execution_readiness(db, run_id, plan, settings_obj,
                                      llm_available=bool(report_llm_client and report_llm_client.is_available())):
        return _summary(store.get_fresh_agent_run(db, run_id))
    steps = plan.get("steps") or []
    observations = load_observations(store.list_tool_traces(db, run_id))
    resume_after_step = run.current_step

    try:
        run = store.mark_agent_run_running_unless_cancelled(db, run_id)
        if run.status == "cancelled":
            return _message_summary(run, "Run was cancelled before execution started.")
        for step in steps:
            if store.is_agent_run_cancelled(db, run_id):
                cancelled = store.get_fresh_agent_run(db, run_id)
                return _message_summary(cancelled, "Run cancelled by user.")
            step_no = int(step.get("step_no") or 0)
            tool_name = str(step.get("tool_name") or "")
            arguments = step.get("arguments") or {}
            if step_no <= resume_after_step:
                continue

            if _step_requires_confirmation(step, tool_name) and not _is_step_confirmed(plan, step_no):
                message = f"Waiting for human confirmation before step {step_no}: {tool_name}"
                run = store.update_agent_run_progress(db, run_id, max(step_no - 1, 0))
                run = store.update_agent_run_status(db, run_id, "waiting_human", message)
                return _message_summary(run, message)

            if tool_name == "report_writer":
                observations.append(
                    {
                        "step_no": step_no,
                        "tool_name": tool_name,
                        "success": True,
                        "output_summary": "Report writer step handled by the structured Reporter.",
                        "error_message": None,
                        "output": {"handled_by": "app.agent.reporter"},
                        "metadata": {},
                    }
                )
                run = store.update_agent_run_progress(db, run_id, step_no)
                continue

            if not is_executable_tool(tool_name):
                result = ToolResult(
                    success=False,
                    error_message=f"Executor does not support tool '{tool_name}'.",
                    metadata={"error_type": "unsupported_tool", "tool_name": tool_name},
                )
                record_tool_result(db, run_id, step_no, tool_name, arguments, result, 0)
                observations.append(_failed_observation(step, result))
                run = store.update_agent_run_progress(db, run_id, step_no, total_tool_calls_delta=1)
                continue

            # Resolve arguments_from references from previous step outputs
            if step.get("arguments_from"):
                if dependency_missing(step, observations):
                    observations.append(skip_dependency(db, run_id, step))
                    continue
                arguments = _resolve_arguments_from(step, observations)

            arguments = prepare_tool_arguments(
                tool_name, arguments, plan, settings_obj
            )
            started = perf_counter()
            result = execute_governed_operation(
                tool_name,
                arguments,
                plan,
                settings_obj,
                execute_tool,
                execution_arguments=(
                    (lambda prepared: file_reader_execution_arguments(prepared, plan))
                    if tool_name == "file_reader"
                    else None
                ),
                arguments_prepared=True,
            )
            latency_ms = int((perf_counter() - started) * 1000)
            trace = record_tool_result(
                db, run_id, step_no, tool_name, arguments, result, latency_ms
            )
            observations.append(
                {
                    "trace_id": trace.trace_id,
                    "step_no": step_no,
                    "tool_name": tool_name,
                    "success": result.success,
                    "output_summary": result.output_summary,
                    "error_message": result.error_message,
                    "output": result.output,
                    "metadata": result.metadata,
                }
            )
            run = store.update_agent_run_progress(
                db,
                run_id,
                step_no,
                total_tool_calls_delta=0 if result.metadata.get("executed") is False else 1,
                latency_ms_delta=latency_ms,
            )

        if store.is_agent_run_cancelled(db, run_id):
            cancelled = store.get_fresh_agent_run(db, run_id)
            return _message_summary(cancelled, "Run cancelled by user.")

        traces = store.list_tool_traces(db, run_id)
        if (
            plan.get("research_mode") == "quick"
            and plan.get("quick_output_mode") != "discovery"
        ):
            # A substantive Quick run gets its bounded alternate-candidate
            # window before the broader operational outcome gate can finalize
            # a fetch-quality failure. Provider, budget, permission, and
            # execution failures remain visible to that gate after retries.
            provenance_bundle = materialize_execution_provenance(
                db, run, plan, observations, traces, settings_obj
            )
            evidence_assessment = assess_required_evidence(
                plan.get("task_contract"), provenance_bundle
            )
            traces, provenance_bundle, evidence_assessment = _run_quick_refetches(
                db, run, plan, settings_obj, observations, traces,
                provenance_bundle, evidence_assessment,
            )
            plan["evidence_assessment"] = evidence_assessment.as_dict()
            store.replace_agent_run_plan(db, run_id, plan)

        if not enforce_research_outcome(db, run, plan, observations, traces, settings_obj):
            return _summary(store.get_fresh_agent_run(db, run_id))
        provenance_bundle = materialize_execution_provenance(
            db,
            run,
            plan,
            observations,
            traces,
            settings_obj,
        )
        evidence_assessment = assess_required_evidence(plan.get("task_contract"), provenance_bundle)
        plan["evidence_assessment"] = evidence_assessment.as_dict()
        if not evidence_assessment.passed:
            # This is deliberately before Reporter: discovery snippets, page
            # shells, and unrelated local text must never reach factual prose.
            # The original traces and structured, retryable gaps remain the
            # diagnostic artifact for a later retry run.
            outcome = dict(plan.get("research_outcome") or {})
            outcome.update({
                "status": "failed",
                "error_code": "required_evidence_coverage_incomplete",
                "message": "Required task-relevant body evidence is incomplete.",
                "evidence_assessment": evidence_assessment.as_dict(),
            })
            plan["research_outcome"] = outcome
            store.replace_agent_run_plan(db, run_id, plan)
            record_trace_event(
                db, run_id, max((trace.step_no for trace in traces), default=0) + 1,
                "required_evidence_gate", "failed", {}, outcome["message"],
                evidence_assessment.as_dict(), error_message=outcome["message"],
            )
            finalize_terminal_decision(db, run, plan, traces=traces)
            return _summary(store.get_fresh_agent_run(db, run_id))
        _llm = resolve_report_llm_client(settings_obj, report_llm_client)
        report_audit = ReportGenerationAudit(db, run_id, traces)
        report_llm_responses = report_audit.responses
        citation_validation_reports: list[Any] = []
        reference_verification_reports: list[Any] = []
        def refresh_report_evidence(feedback: dict[str, Any]) -> dict[str, Any] | None:
            nonlocal traces, provenance_bundle
            from app.agent.budget import acquisition_budget, FinalizationRequired
            if not (feedback.get("must_remove_or_rewrite_unsupported")
                    or feedback.get("weak_citations_to_improve_only_if_needed")):
                return None
            before = len((plan.get("quick_refetch") or {}).get("attempts") or [])
            try:
                with acquisition_budget():
                    refreshed_traces, provenance_bundle, _ = _run_quick_refetches(
                        db, store.get_fresh_agent_run(db, run_id), plan, settings_obj,
                        observations, store.list_tool_traces(db, run_id), provenance_bundle,
                        assess_required_evidence(plan.get("task_contract"), provenance_bundle),
                        report_feedback=True,
                    )
                    traces[:] = refreshed_traces
            except FinalizationRequired:
                # Preserve the existing candidate and remaining report budget.
                # Hard exhaustion/cancellation still escapes normally.
                return None
            after = len((plan.get("quick_refetch") or {}).get("attempts") or [])
            return provenance_bundle if after > before else None
        try:
            markdown = generate_markdown_report(
                report_subject(run),
                plan,
                observations,
                traces,
                llm_client=_llm,
                provenance_bundle=provenance_bundle,
                report_type=run.report_type,
                usage_callback=report_audit.usage_callback,
                citation_validation_callback=citation_validation_reports.append,
                reference_verification_callback=reference_verification_reports.append,
                revision_attempt_callback=report_audit.persist_attempt,
                cancellation_check=lambda: check_report_generation_not_cancelled(db, run_id),
                evidence_refresh_callback=refresh_report_evidence,
            )
        finally:
            # ReportGenerationAudit records each provider attempt and usage.
            pass
        draft_result = plan.get("report_draft_result")
        if report_llm_responses:
            traces = store.list_tool_traces(db, run_id)
        report_path = save_report(run_id, markdown)
        run = store.update_agent_run_report(db, run_id, report_path)
        run = _persist_citation_validation(
            db,
            run_id,
            citation_validation_reports,
            traces,
        )
        traces = store.list_tool_traces(db, run_id)
        run = _persist_reference_verification(
            db,
            run_id,
            reference_verification_reports,
            traces,
        )
        traces = store.list_tool_traces(db, run_id)

        plan = json.loads(run.plan_json or "{}")
        if isinstance(draft_result, dict):
            plan["report_draft_result"] = draft_result
        if report_audit.attempts:
            plan["report_revision_attempts"] = report_audit.attempts
        plan["report_generation"] = {
            **report_audit.manifest(),
            "adopted": bool((plan.get("report_draft_result") or {}).get("adopted")),
            "validator_version": validator_version_for(citation_validation_reports[-1] if citation_validation_reports else None),
        }
        _persist_final_report_gate(
            db, run, plan, markdown, provenance_bundle, report_path,
            citation_validation_reports,
        )
        run = store.get_fresh_agent_run(db, run_id)
        # Persist an explicit limitation in the artifact itself whenever the
        # final gate rejects it.  Re-save and rematerialize so the revision and
        # terminal hash describe this exact body.
        gate = json.loads(run.plan_json or {}).get("report_integrity") or {}
        if gate.get("status") == "failed" and "未完成" not in markdown:
            markdown = markdown.rstrip() + "\n\n## 12. 完成状态审计\n\n> 本报告未完成最终研究完整性核验，内容仅作为审计中间结果。\n"
            report_path = save_report(run_id, markdown)
            run = store.update_agent_run_report(db, run_id, report_path)
            plan = json.loads(run.plan_json or "{}")
            _persist_final_report_gate(
                db, run, plan, markdown, provenance_bundle, report_path,
                citation_validation_reports,
            )
            run = store.get_fresh_agent_run(db, run_id)

        if store.is_agent_run_cancelled(db, run_id):
            cancelled = store.get_fresh_agent_run(db, run_id)
            return _message_summary(cancelled, "Run cancelled by user.")
        if completion_status != "running":
            plan = json.loads(run.plan_json or "{}")
            finalize_terminal_decision(db, run, plan, traces=traces)
            run = store.get_fresh_agent_run(db, run_id)
            # Incomplete state is represented by the terminal decision and
            # UI; never rewrite the report after its hash was finalized.

        # ── Phase 6: Summarize LLM token/cost from traces ─────────────
        try:
            from app.llm.cost import estimate_cost_from_tokens
            llm_for_cost = resolve_report_llm_client(settings_obj, report_llm_client)
            if llm_for_cost is not None and llm_for_cost.is_available():
                llm_desc = llm_for_cost.describe()
                provider = llm_desc.get("provider", "unknown")
                model = llm_desc.get("model")
                total_ti = sum(t.token_in or 0 for t in traces)
                total_to = sum(t.token_out or 0 for t in traces)
                cost = estimate_cost_from_tokens(provider, model, total_ti, total_to)
                run = store.update_agent_run_cost(
                    db,
                    run_id,
                    token_in=total_ti,
                    token_out=total_to,
                    estimated_cost=cost,
                )
        except Exception:
            pass  # Cost tracking failure must not block run completion

        if run.status == "completed":
            _after_run_completed(db, run, markdown, step_no=0)

        return _summary(run)
    except Exception as exc:
        if store.is_agent_run_cancelled(db, run_id):
            cancelled = store.get_fresh_agent_run(db, run_id)
            return _message_summary(cancelled, "Run cancelled by user.")
        db.rollback()
        run = fail_execution(db, run_id, exc)
        return _summary(run)
