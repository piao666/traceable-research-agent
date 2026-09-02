"""Manual executor for deterministic plans."""

from __future__ import annotations

import json
import logging
from time import perf_counter
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from app.agent.file_access_policy import file_reader_execution_arguments
from app.agent.preflight import enforce_execution_readiness
from app.agent.outcome import dependency_missing, enforce_research_outcome, fail_execution, load_observations, report_subject, result_integrity, skip_dependency
from app.agent.report_generation import resolve_report_llm_client
from app.agent.reporter import generate_markdown_report, save_report
from app.agent.source_governance import (
    execute_targeted_refetches,
    govern_tool_result,
    persisted_refetch_rounds,
    prepare_tool_arguments,
)
from app.config import Settings, settings as _exec_settings
from app.evidence.service import materialize_execution_provenance
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
    if validation.llm_used:
        try:
            from app.llm.cost import estimate_cost_from_tokens

            estimated_cost = estimate_cost_from_tokens(
                validation.llm_provider or "unknown",
                validation.llm_model,
                validation.token_in,
                validation.token_out,
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
        token_in=validation.token_in,
        token_out=validation.token_out,
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


def _check_profile_quota(
    db: Session,
    run_id: str,
    plan: dict[str, Any],
    provenance_bundle: dict[str, Any] | None,
    traces: list[Any],
) -> None:
    """Phase 8.1: Check retrieval profile constraints and emit shortfall trace."""
    profile_constraints = (plan.get("profile_constraints") or {})
    if not profile_constraints:
        return

    if not provenance_bundle:
        return

    documents = [doc for doc in provenance_bundle.get("source_documents") or []
                 if (doc.get("metadata") or {}).get("research_eligible")
                 and not (doc.get("metadata") or {}).get("is_mock")
                 and not (doc.get("metadata") or {}).get("is_fallback")]
    documents = list({doc.get("canonical_uri"): doc for doc in documents}.values())

    try:
        from app.evidence.policy import T0, T1, T2

        tier_counts = {T0: 0, T1: 0, T2: 0}
        cluster_ids: set[str] = set()
        domain_counts: dict[str, int] = {}
        for doc in documents:
            metadata = doc.get("metadata") or {}
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except Exception:
                    metadata = {}
            tier = metadata.get("source_tier", T2)
            if tier in tier_counts:
                tier_counts[tier] += 1
            cluster = str(metadata.get("source_cluster_id") or "").strip()
            if cluster:
                cluster_ids.add(cluster)
            hostname = str(
                metadata.get("hostname")
                or metadata.get("source_hostname")
                or urlsplit(str(doc.get("canonical_uri") or "")).hostname
                or ""
            ).strip().lower()
            if hostname:
                domain_counts[hostname] = domain_counts.get(hostname, 0) + 1
                if not cluster:
                    cluster_ids.add(hostname)
            elif not cluster:
                cluster_ids.add(str(doc.get("canonical_uri") or doc.get("document_id")))

        total = len(documents)
        t0 = tier_counts[T0]
        t1 = tier_counts[T1]
        t2 = tier_counts[T2]
        independent = len(cluster_ids)

        min_t0 = int(profile_constraints.get("min_t0_sources", 1))
        min_independent = int(profile_constraints.get("min_independent_sources", 2))
        min_t2 = int(profile_constraints.get("min_t2_sources", 0))
        max_t2_ratio = float(profile_constraints.get("max_t2_ratio", 0.50))
        max_per_domain = profile_constraints.get("max_per_domain")
        shortfall_policy = profile_constraints.get("shortfall_policy", "report_only")

        t0_shortfall = max(0, min_t0 - t0)
        independent_shortfall = max(0, min_independent - independent)
        t2_shortfall = max(0, min_t2 - t2)
        t2_ratio = (t2 / total) if total else 0.0
        t2_ratio_exceeded = t2_ratio > max_t2_ratio
        domain_overages = [
            domain for domain, count in domain_counts.items()
            if max_per_domain and count > int(max_per_domain)
        ]

        quota_info = {
            "profile": plan.get("retrieval_profile", "generic"),
            "total_sources": total,
            "t0_required": min_t0,
            "t0_achieved": t0,
            "t1_count": t1,
            "t2_count": t2,
            "independent_required": min_independent,
            "independent_achieved": independent,
            "max_t2_ratio": max_t2_ratio,
            "t2_ratio": round(t2_ratio, 4),
            "t2_required": min_t2,
            "max_per_domain": max_per_domain,
            "domain_overages": domain_overages,
            "shortfalls": {
                "t0_shortfall": t0_shortfall,
                "independent_shortfall": independent_shortfall,
                "t2_shortfall": t2_shortfall,
                "t2_ratio_exceeded": t2_ratio_exceeded,
            },
            "shortfall_policy": shortfall_policy,
        }

        has_shortfall = bool(
            t0_shortfall
            or independent_shortfall
            or t2_shortfall
            or t2_ratio_exceeded
            or domain_overages
        )
        if has_shortfall:
            outcome = plan.get("research_outcome") or {}
            warning = "Source quota requirements were not met; see source_quota_check in Trace."
            outcome["warnings"] = list(dict.fromkeys([*(outcome.get("warnings") or []), warning]))
            plan["research_outcome"] = outcome
            store.replace_agent_run_plan(db, run_id, plan)
            record_trace_event(
                db=db,
                run_id=run_id,
                step_no=max((trace.step_no for trace in traces), default=0) + 1,
                tool_name="source_quota_check",
                status="warning",
                input_data={"profile_constraints": profile_constraints},
                output_summary=(
                    f"Source quota shortfall: T0={t0}/{min_t0}, "
                    f"independent={independent}/{min_independent}, "
                    f"T2={t2} (ratio {t2_ratio:.2f}), policy={shortfall_policy}"
                ),
                output_data=quota_info,
            )
    except Exception as exc:
        logging.getLogger(__name__).warning("Source quota check failed: %s", exc)


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
    refetch_rounds_used = persisted_refetch_rounds(store.list_tool_traces(db, run_id))

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
                tool_name,
                arguments,
                plan,
                settings_obj,
            )
            execution_arguments = (
                file_reader_execution_arguments(arguments, plan)
                if tool_name == "file_reader"
                else arguments
            )
            started = perf_counter()
            result = execute_tool(tool_name, execution_arguments)
            latency_ms = int((perf_counter() - started) * 1000)
            result = govern_tool_result(tool_name, result, plan, settings_obj)
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
                total_tool_calls_delta=1,
                latency_ms_delta=latency_ms,
            )

            def _execute_refetch(name: str, refetch_args: dict[str, Any]) -> tuple[ToolResult, int]:
                refetch_started = perf_counter()
                refetch_result = execute_tool(name, refetch_args)
                return refetch_result, int((perf_counter() - refetch_started) * 1000)

            refetches = execute_targeted_refetches(
                tool_name,
                arguments,
                result,
                plan,
                settings_obj,
                execute=_execute_refetch,
                max_rounds=settings_obj.max_refetch_rounds - refetch_rounds_used,
                starting_round=refetch_rounds_used,
            )
            refetch_rounds_used += len(refetches)
            for refetch in refetches:
                trace = record_tool_result(
                    db,
                    run_id,
                    step_no,
                    tool_name,
                    refetch.arguments,
                    refetch.result,
                    refetch.latency_ms,
                    sub_query=f"source_refetch_round:{refetch.round_no}",
                )
                observations.append(
                    {
                        "trace_id": trace.trace_id,
                        "step_no": step_no,
                        "tool_name": tool_name,
                        "success": refetch.result.success,
                        "output_summary": refetch.result.output_summary,
                        "error_message": refetch.result.error_message,
                        "output": refetch.result.output,
                        "metadata": refetch.result.metadata,
                    }
                )
                run = store.update_agent_run_progress(
                    db,
                    run_id,
                    step_no,
                    total_tool_calls_delta=1,
                    latency_ms_delta=refetch.latency_ms,
                )

        if store.is_agent_run_cancelled(db, run_id):
            cancelled = store.get_fresh_agent_run(db, run_id)
            return _message_summary(cancelled, "Run cancelled by user.")

        traces = store.list_tool_traces(db, run_id)
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
        _check_profile_quota(db, run_id, plan, provenance_bundle, traces)
        _llm = resolve_report_llm_client(settings_obj, report_llm_client)
        report_llm_responses: list[Any] = []
        citation_validation_reports: list[Any] = []
        reference_verification_reports: list[Any] = []
        markdown = generate_markdown_report(
            report_subject(run),
            plan,
            observations,
            traces,
            llm_client=_llm,
            provenance_bundle=provenance_bundle,
            report_type=run.report_type,
            usage_callback=report_llm_responses.append,
            citation_validation_callback=citation_validation_reports.append,
            reference_verification_callback=reference_verification_reports.append,
        )
        if report_llm_responses:
            from app.llm.cost import estimate_cost

            response = report_llm_responses[-1]
            usage = response.usage
            report_cost = estimate_cost(response.provider, response.model, usage)
            record_trace_event(
                db=db,
                run_id=run_id,
                step_no=max((trace.step_no for trace in traces), default=0) + 1,
                tool_name="report_synthesis",
                status="success",
                input_data={"provider": response.provider, "model": response.model},
                output_summary="LLM report synthesis completed.",
                output_data={"provider": response.provider, "model": response.model},
                token_in=usage.prompt_tokens,
                token_out=usage.completion_tokens,
                estimated_cost=report_cost,
            )
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

        if store.is_agent_run_cancelled(db, run_id):
            cancelled = store.get_fresh_agent_run(db, run_id)
            return _message_summary(cancelled, "Run cancelled by user.")
        run = store.update_agent_run_status(db, run_id, completion_status, None)

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

        _after_run_completed(db, run, markdown, step_no=0)

        return _summary(run)
    except Exception as exc:
        if store.is_agent_run_cancelled(db, run_id):
            cancelled = store.get_fresh_agent_run(db, run_id)
            return _message_summary(cancelled, "Run cancelled by user.")
        db.rollback()
        run = fail_execution(db, run_id, exc)
        return _summary(run)
