"""Iterative deepening loop for deep research (Phase 5).

Wraps the ReAct executor in a multi-round cycle:
  Round 0: initial ReAct run with the user's original task
  Round N: LLM synthesizes learnings → follow_up_queries → new sub-queries → ReAct run

Stops when MAX_DEPTH is reached, follow_up_queries is empty, or LLM is unavailable.
Each round's learnings are persisted as trace events.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.agent.react_executor import run_react_task
from app.agent.reporter import generate_markdown_report, save_report
from app.config import Settings, settings as _settings
from app.evidence.service import materialize_execution_provenance
from app.llm.base import LLMClient, LLMMessage
from app.llm.providers import create_llm_client
from app.trace import store
from app.trace.logger import record_trace_event
from app.trace.models import AgentRun


_DEEPENING_SYSTEM = """You are a research director reviewing the results of a deep research run.
Your job is to extract structured learnings and generate follow-up queries to fill knowledge gaps.

Rules:
- Extract 2-5 concrete, factual learnings from the observations. Each learning should be one sentence.
- Generate 1-{breadth} follow-up queries that would deepen the research. Focus on gaps, contradictions, or areas needing more evidence.
- If the evidence is already comprehensive, return an empty follow_up_queries list.
- Output ONLY valid JSON with the schema below, no other text.

Schema:
{{"learnings": ["string", ...], "follow_up_queries": ["string", ...], "is_comprehensive": false}}"""


def _build_deepening_messages(
    task: str,
    observations: list[dict[str, Any]],
    prior_learnings: list[str],
    breadth: int,
) -> list[LLMMessage]:
    """Build messages for the deepening synthesis LLM call."""
    # Build a compact observation summary
    obs_parts: list[str] = []
    for obs in observations[-20:]:  # Last 20 observations max
        tool = obs.get("tool_name") or obs.get("action") or "unknown"
        summary = obs.get("output_summary") or obs.get("observation_summary") or ""
        success = "✅" if obs.get("success") else "❌"
        obs_parts.append(f"[{success} {tool}] {str(summary)[:300]}")
    obs_text = "\n".join(obs_parts) if obs_parts else "(no observations)"

    prior_text = ""
    if prior_learnings:
        prior_text = "\n".join(f"- {l}" for l in prior_learnings[-20:])
        prior_text = f"\nPrior round learnings:\n{prior_text}\n"

    user_msg = (
        f"Original task: {task}\n\n"
        f"Tool observations from this round:\n{obs_text}\n"
        f"{prior_text}"
        f"Extract learnings and follow-up queries (max {breadth}). "
        "If comprehensive, set follow_up_queries=[] and is_comprehensive=true."
    )
    return [
        LLMMessage(role="system", content=_DEEPENING_SYSTEM.replace("{breadth}", str(breadth))),
        LLMMessage(role="user", content=user_msg),
    ]


def _parse_deepening_response(content: str) -> dict[str, Any]:
    """Parse the LLM deepening response, with defensive fallback."""
    try:
        # Extract JSON object from potential markdown wrapping
        text = content.strip()
        if "```" in text:
            # Extract content between ```json and ```
            import re
            match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
            if match:
                text = match.group(1).strip()
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            return {"learnings": [], "follow_up_queries": [], "is_comprehensive": True}
        return {
            "learnings": [str(l) for l in parsed.get("learnings") or [] if l],
            "follow_up_queries": [str(q) for q in parsed.get("follow_up_queries") or [] if q],
            "is_comprehensive": bool(parsed.get("is_comprehensive")),
        }
    except (json.JSONDecodeError, TypeError, AttributeError):
        return {"learnings": [], "follow_up_queries": [], "is_comprehensive": True}


def _record_deepening_round_trace(
    db: Session,
    run_id: str,
    round_num: int,
    learnings: list[str],
    follow_up_queries: list[str],
    is_comprehensive: bool,
    sub_run_ids: list[str],
) -> None:
    """Record a deepening round as a trace event."""
    record_trace_event(
        db=db,
        run_id=run_id,
        step_no=round_num,
        tool_name="deepening_round",
        status="success",
        input_data={"round": round_num},
        output_summary=(
            f"Deepening round {round_num}: {len(learnings)} learnings, "
            f"{len(follow_up_queries)} follow-ups"
            + (" (comprehensive)" if is_comprehensive else "")
        ),
        output_data={
            "round": round_num,
            "learnings": learnings,
            "follow_up_queries": follow_up_queries,
            "is_comprehensive": is_comprehensive,
            "sub_run_ids": sub_run_ids,
        },
    )


def _run_single_round(
    db: Session,
    parent_run_id: str,
    task: str,
    sub_queries: list[str],
    settings_obj: Settings,
    llm_client: LLMClient | None = None,
) -> list[dict[str, Any]]:
    """Execute one deepening round: create sub-runs for each follow-up query.

    Returns a list of observation dicts from all sub-runs.
    """
    all_observations: list[dict[str, Any]] = []
    sub_run_ids: list[str] = []

    for sq in sub_queries:
        # Create a sub-run for this follow-up query
        sub_run_id = store.create_agent_run(
            db,
            task=sq,
            plan_json=json.dumps({
                "version": "deepening-v1",
                "task": sq,
                "execution_mode": "react",
                "parent_run_id": parent_run_id,
                "notes": [f"Deepening follow-up from run {parent_run_id}"],
            }),
            allowed_tools_json=None,
            session_id=None,
        )
        if sub_run_id is None:
            continue
        sub_run_ids.append(sub_run_id)

        try:
            result = run_react_task(db, sub_run_id, settings_obj, llm_client)
        except Exception:
            result = {"status": "failed", "run_id": sub_run_id}

        # Collect observations from the sub-run
        sub_run = store.get_agent_run(db, sub_run_id)
        if sub_run is not None:
            traces = store.list_tool_traces(db, sub_run_id)
            for trace in traces:
                all_observations.append({
                    "step_no": trace.step_no,
                    "tool_name": trace.tool_name,
                    "success": trace.status == "success",
                    "output_summary": trace.output_summary,
                    "error_message": trace.error_message,
                    "output": json.loads(trace.output_json) if trace.output_json else {},
                    "metadata": {"sub_run_id": sub_run_id},
                })

    return all_observations


def run_deepening(
    db: Session,
    run_id: str,
    settings_obj: Settings = _settings,
    llm_client: LLMClient | None = None,
) -> dict[str, Any]:
    """Execute iterative deepening research.

    Entry point called by the API run endpoint when DEEP_RESEARCH_ENABLED=true
    and execution_mode=react.

    Returns the summary dict of the final (parent) run.
    """
    run = store.get_agent_run(db, run_id)
    if run is None:
        raise ValueError("Task run not found.")

    client = llm_client or create_llm_client(settings_obj)

    if not client.is_available():
        # No LLM → fall back to single-round ReAct
        return run_react_task(db, run_id, settings_obj, client)

    max_depth = settings_obj.deep_research_max_depth
    breadth = settings_obj.deep_research_breadth

    all_learnings: list[str] = []
    all_observations: list[dict[str, Any]] = []
    all_sub_run_ids: list[str] = []
    current_task = run.task

    # Round 0: initial ReAct run
    initial_result = run_react_task(db, run_id, settings_obj, client)
    run = store.get_agent_run(db, run_id)
    if run is None:
        return initial_result

    # Collect initial observations
    traces = store.list_tool_traces(db, run_id)
    for trace in traces:
        all_observations.append({
            "step_no": trace.step_no,
            "tool_name": trace.tool_name,
            "success": trace.status == "success",
            "output_summary": trace.output_summary,
            "error_message": trace.error_message,
            "output": json.loads(trace.output_json) if trace.output_json else {},
            "metadata": {},
        })

    # Deepening rounds
    for round_num in range(1, max_depth + 1):
        # Ask LLM for learnings + follow_up_queries
        messages = _build_deepening_messages(
            current_task, all_observations, all_learnings, breadth,
        )
        response = client.complete(messages, temperature=0.0, max_tokens=1200)

        if not response.success or not response.content:
            # LLM call failed → stop deepening
            _record_deepening_round_trace(
                db, run_id, round_num, [], [], True, [],
            )
            break

        deepening = _parse_deepening_response(response.content)
        learnings = deepening.get("learnings") or []
        follow_ups = deepening.get("follow_up_queries") or []
        is_comprehensive = bool(deepening.get("is_comprehensive"))

        all_learnings.extend(learnings)

        if not follow_ups or is_comprehensive:
            _record_deepening_round_trace(
                db, run_id, round_num, learnings, follow_ups, is_comprehensive, [],
            )
            break

        # Execute follow-up queries
        round_obs = _run_single_round(
            db, run_id, current_task, follow_ups[:breadth], settings_obj, client,
        )
        all_observations.extend(round_obs)

        sub_ids = [
            obs.get("metadata", {}).get("sub_run_id", "")
            for obs in round_obs
        ]
        all_sub_run_ids.extend([s for s in sub_ids if s])

        _record_deepening_round_trace(
            db, run_id, round_num, learnings, follow_ups, is_comprehensive,
            [s for s in sub_ids if s],
        )

    # Store learnings in the parent run's plan
    run = store.get_agent_run(db, run_id)
    if run is not None and run.plan_json:
        plan = json.loads(run.plan_json)
        plan["deepening_learnings"] = all_learnings
        plan["deepening_sub_run_ids"] = all_sub_run_ids
        plan["deepening_total_rounds"] = min(round_num, max_depth) if 'round_num' in dir() else 0
        store.replace_agent_run_plan(db, run_id, plan)

    # Regenerate the final report with all accumulated evidence
    run = store.get_agent_run(db, run_id)
    if run is None:
        raise ValueError("Task run not found.")

    all_traces = store.list_tool_traces(db, run_id)
    plan = json.loads(run.plan_json) if run.plan_json else {}

    provenance_bundle = materialize_execution_provenance(
        db, run, plan,
        [
            obs for obs in all_observations
            if not obs.get("metadata", {}).get("sub_run_id")  # parent run observations only
        ],
        [t for t in all_traces if t.run_id == run_id],
        settings_obj,
    )

    from app.agent.report_generation import resolve_report_llm_client
    _llm = resolve_report_llm_client(settings_obj, client)
    markdown = generate_markdown_report(
        run, plan,
        [
            obs for obs in all_observations
            if not obs.get("metadata", {}).get("sub_run_id")
        ],
        [t for t in all_traces if t.run_id == run_id],
        llm_client=_llm,
        provenance_bundle=provenance_bundle,
        report_type=run.report_type,
    )
    report_path = save_report(run_id, markdown)
    store.update_agent_run_report(db, run_id, report_path)

    # Add deepening summary to report markdown
    if all_learnings:
        deepening_section = "\n\n## 10. 研究深化过程\n\n"
        deepening_section += f"* 深化轮数: {plan.get('deepening_total_rounds', 0)}\n"
        deepening_section += f"* 累计学习: {len(all_learnings)} 条\n\n"
        deepening_section += "### 各轮学习\n\n"
        for i, learning in enumerate(all_learnings, 1):
            deepening_section += f"{i}. {learning}\n"
        deepening_section += "\n"
        markdown += deepening_section
        save_report(run_id, markdown)

    return {
        "run_id": run.run_id,
        "status": run.status,
        "current_step": run.current_step,
        "total_steps": run.total_steps,
        "total_tool_calls": run.total_tool_calls,
        "report_url": f"/api/reports/{run.run_id}",
        "trace_url": f"/api/tasks/{run.run_id}/trace",
        "error_message": run.error_message,
        "message": "Deepening research completed.",
        "execution_mode": "react_deepening",
        "deepening_rounds": plan.get("deepening_total_rounds", 0),
        "deepening_learnings_count": len(all_learnings),
    }
