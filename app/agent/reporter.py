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
        selected = [
            {
                "source": hit.get("source"),
                "chunk_id": hit.get("chunk_id"),
                "score": hit.get("score"),
                "text": str(hit.get("text") or "")[:240],
            }
            for hit in hits[:5]
        ]
        return _json_preview(selected, max_chars=1200)
    return _json_preview(output)


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

    lines.extend(["## Evidence And Observations", ""])
    if observations:
        for observation in observations:
            lines.extend(
                [
                    f"### Step {observation.get('step_no')}: {observation.get('tool_name')}",
                    "",
                    f"* success: `{observation.get('success')}`",
                    f"* output_summary: {observation.get('output_summary') or '<none>'}",
                    f"* error_message: {observation.get('error_message') or '<none>'}",
                    "",
                    "```text",
                    _selected_evidence(
                        str(observation.get("tool_name") or ""),
                        observation.get("output"),
                    ),
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

    lines.extend(
        [
            "",
            "## Limitations",
            "",
            "* deterministic planner",
            "* no LLM reasoning yet",
            "* no MCP/GitHub",
            "* no HITL",
            "* report generated from tool observations only",
            "",
        ]
    )
    return "\n".join(lines)


def save_report(run_id: str, markdown: str) -> str:
    """Save Markdown report and return a repository-relative path."""

    REPORTS_ROOT.mkdir(parents=True, exist_ok=True)
    path = REPORTS_ROOT / f"{run_id}.md"
    path.write_text(markdown, encoding="utf-8")
    return str(path.relative_to(ROOT))
