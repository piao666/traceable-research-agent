"""Markdown report generation from deterministic run observations."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from app.trace.models import AgentRun, ToolTrace


ROOT = Path(__file__).resolve().parents[2]
REPORTS_ROOT = ROOT / "workspace" / "reports"


def _json_preview(data: Any, max_chars: int = 500) -> str:
    text = json.dumps(data, ensure_ascii=False, default=str, indent=2)
    return text if len(text) <= max_chars else text[: max_chars - 3] + "..."


def _selected_evidence(tool_name: str, output: Any) -> str:
    if not isinstance(output, dict):
        return _json_preview(output)

    if tool_name == "file_reader":
        content = str(output.get("content") or "")
        return content[:500] + ("..." if len(content) > 500 else "")
    if tool_name == "sql_query":
        columns = output.get("columns") or []
        rows = output.get("rows") or []
        return _json_preview({"columns": columns, "rows": rows[:5]}, max_chars=900)
    if tool_name == "rag_search":
        hits = output.get("hits") or []
        rag_metadata = output.get("metadata") if isinstance(output.get("metadata"), dict) else {}
        selected = [
            {
                "source": hit.get("source"),
                "chunk_id": hit.get("chunk_id"),
                "score": hit.get("score"),
                "text": str(hit.get("text") or "")[:240],
                "rrf_score": (hit.get("metadata") or {}).get("rrf_score"),
            }
            for hit in hits[:5]
        ]
        return _json_preview(
            {
                "retrieval": {
                    key: rag_metadata.get(key)
                    for key in (
                        "retrieval_mode",
                        "dense_hit_count",
                        "bm25_hit_count",
                        "rrf_k",
                        "fallback_used",
                    )
                    if key in rag_metadata
                },
                "hits": selected,
            },
            max_chars=1400,
        )
    if tool_name == "mcp_github_search":
        results = output.get("results") or []
        selected = [
            {
                "title": result.get("title"),
                "url": result.get("url"),
                "type": result.get("type"),
                "source": result.get("source"),
                "snippet": str(result.get("snippet") or "")[:240],
            }
            for result in results[:5]
        ]
        return _json_preview(selected, max_chars=1200)
    return _json_preview(output)


def _runtime_limitations(plan: dict[str, Any]) -> list[str]:
    planner_source = plan.get("planner_source") or "deterministic"
    if planner_source == "llm":
        planner_lines = [
            "LLM Planner is enabled.",
            "deterministic fallback is still available for reliability.",
            "generated report is based on tool observations and traces.",
        ]
    elif planner_source == "deterministic_fallback":
        planner_lines = [
            "LLM Planner was attempted but deterministic fallback was used.",
            "fallback reason is recorded in planning notes when available.",
            "generated report is based on tool observations and traces.",
        ]
    else:
        planner_lines = [
            "deterministic planner is used.",
            "no LLM planning is active for this run.",
            "generated report is based on tool observations and traces.",
        ]
    execution_mode = plan.get("execution_mode") or "planned"
    react_state = plan.get("react_state") if isinstance(plan.get("react_state"), dict) else {}
    if execution_mode == "react":
        planner_lines += [
            "ReAct decisions are bounded by max_steps and same_tool_max_calls.",
            "Decision rationale is stored as a concise summary rather than raw model output.",
        ]
    if react_state.get("fallback_used"):
        planner_lines.append("ReAct fallback_used=true; the persisted planned executor completed the run.")
    if react_state.get("completed_with_limitation"):
        planner_lines.append("The ReAct run completed with a recorded runtime limitation.")
    return planner_lines + [
        "GitHub/MCP path is read-only and defaults to deterministic mock mode.",
        "HITL is a minimal status and confirmation flow, not production auth.",
        "generated reports and runtime indexes are local ignored artifacts.",
    ]


def generate_markdown_report(
    run: AgentRun,
    plan: dict[str, Any],
    observations: list[dict[str, Any]],
    traces: list[ToolTrace],
) -> str:
    """Build a deterministic Markdown report from persisted run evidence."""

    status_counts = Counter(trace.status for trace in traces)
    lines: list[str] = [
        "# Traceable Research Report",
        "",
        "## Task",
        "",
        run.task,
        "",
        "## Run Summary",
        "",
        f"* run_id: `{run.run_id}`",
        f"* status: `{run.status}`",
        f"* total_steps: {run.total_steps}",
        f"* executed_tool_calls: {run.total_tool_calls}",
        f"* planner_source: `{plan.get('planner_source') or 'unknown'}`",
        f"* llm_provider: `{plan.get('llm_provider') or '<none>'}`",
        f"* llm_model: `{plan.get('llm_model') or '<none>'}`",
        f"* execution_mode: `{plan.get('execution_mode') or 'planned'}`",
        f"* requested_execution_mode: `{plan.get('requested_execution_mode') or plan.get('execution_mode') or 'planned'}`",
        f"* fallback_used: `{bool((plan.get('react_state') or {}).get('fallback_used'))}`",
        "",
        "## Plan",
        "",
    ]

    for step in plan.get("steps", []):
        lines.extend(
            [
                f"### Step {step.get('step_no')}: {step.get('tool_name')}",
                "",
                f"* goal: {step.get('goal')}",
                f"* arguments: `{json.dumps(step.get('arguments', {}), ensure_ascii=False)}`",
                f"* completion_criteria: {step.get('completion_criteria')}",
                "",
            ]
        )

    notes = plan.get("notes") or []
    if notes:
        lines.extend(["### Planning Notes", ""])
        lines.extend([f"* {note}" for note in notes])
        lines.append("")
    if not plan.get("steps"):
        lines.extend(["No executable plan steps were generated.", ""])

    confirmation = plan.get("confirmation")
    if isinstance(confirmation, dict) and confirmation:
        lines.extend(
            [
                "## Human Confirmation",
                "",
                f"* required_step_no: {confirmation.get('required_step_no')}",
                f"* required_tool_name: {confirmation.get('required_tool_name')}",
                f"* approved: `{confirmation.get('approved')}`",
                f"* comment: {confirmation.get('comment') or '<none>'}",
                f"* approved_at: {confirmation.get('approved_at') or '<none>'}",
                "",
            ]
        )

    react_state = plan.get("react_state")
    react_observations = (
        react_state.get("observation_history")
        if isinstance(react_state, dict)
        else None
    )
    if react_observations:
        lines.extend(["## ReAct Steps / Decision Trace", ""])
        for observation in react_observations:
            lines.extend(
                [
                    f"### ReAct Step {observation.get('step_no')}",
                    "",
                    f"* thought: {str(observation.get('thought') or '<none>')[:500]}",
                    f"* action: `{observation.get('action') or '<none>'}`",
                    f"* observation: {observation.get('observation_summary') or '<none>'}",
                    f"* success: `{observation.get('success')}`",
                    f"* error: {observation.get('error_message') or '<none>'}",
                    "",
                ]
            )

    lines.extend(["## Evidence And Observations", ""])
    if observations:
        for observation in observations:
            tool_name = str(
                observation.get("tool_name") or observation.get("action") or "unknown"
            )
            output_summary = (
                observation.get("output_summary")
                or observation.get("observation_summary")
            )
            lines.extend(
                [
                    f"### Step {observation.get('step_no')}: {tool_name}",
                    "",
                    f"* success: `{observation.get('success')}`",
                    f"* output_summary: {output_summary or '<none>'}",
                    f"* error_message: {observation.get('error_message') or '<none>'}",
                    "",
                    "```text",
                    _selected_evidence(
                        tool_name,
                        observation.get("output"),
                    ),
                    "```",
                    "",
                ]
            )
            metadata = observation.get("metadata") or observation.get("tool_result_metadata")
            if isinstance(metadata, dict) and metadata:
                lines.extend(
                    [
                        "Metadata:",
                        "",
                        "```json",
                        _json_preview(metadata, max_chars=1600),
                        "```",
                        "",
                    ]
                )
    else:
        lines.extend(["No executable tool observations were recorded.", ""])

    lines.extend(["## Trace Summary", ""])
    if status_counts:
        for status, count in sorted(status_counts.items()):
            lines.append(f"* {status}: {count}")
        lines.append("")
    else:
        lines.extend(["No trace rows were recorded.", ""])

    for trace in traces:
        lines.append(
            f"* `{trace.trace_id}` step={trace.step_no} tool={trace.tool_name} "
            f"status={trace.status}"
        )

    problem_traces = [trace for trace in traces if trace.status in {"failed", "rejected"}]
    if problem_traces:
        lines.extend(["", "## Failure / Rejection Details", ""])
        for trace in problem_traces:
            lines.extend(
                [
                    f"### Step {trace.step_no}: {trace.tool_name}",
                    "",
                    f"* status: `{trace.status}`",
                    f"* error_message: {trace.error_message or '<none>'}",
                    f"* output_summary: {trace.output_summary or '<none>'}",
                    "",
                ]
            )

    lines.extend(["", "## Runtime Limitations", ""])
    lines.extend([f"* {limitation}" for limitation in _runtime_limitations(plan)])
    lines.append("")
    return "\n".join(lines)


def save_report(run_id: str, markdown: str) -> str:
    """Save Markdown report and return a repository-relative path."""

    REPORTS_ROOT.mkdir(parents=True, exist_ok=True)
    path = REPORTS_ROOT / f"{run_id}.md"
    path.write_text(markdown, encoding="utf-8")
    return str(path.relative_to(ROOT))
