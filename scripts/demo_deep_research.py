"""Demonstrate the full deep research pipeline with one command.

Usage:
    python scripts/demo_deep_research.py
    python scripts/demo_deep_research.py --question "对比 PyTorch 和 TensorFlow"

Demonstrates: create task → plan → execute tools → generate report → show trace
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agent.executor import run_plan
from app.agent.planner import plan_task
from app.database import SessionLocal, init_db
from app.tools.defaults import register_default_tools
from app.trace import store

PRESET_QUESTIONS = [
    "对比 LangGraph、CrewAI 和 AutoGen 的 Agent 编排能力",
    "2024年大模型安全领域最重要的5篇论文",
    "向量数据库 Milvus vs Qdrant vs Weaviate 技术对比",
]

STAGE_EMOJIS = {
    "init": "🔧",
    "plan": "📋",
    "execute": "⚙️",
    "report": "📝",
    "trace": "🔍",
    "done": "✅",
    "error": "❌",
}


def _print_stage(stage: str, message: str) -> None:
    emoji = STAGE_EMOJIS.get(stage, "•")
    print(f"  {emoji} {message}")


def _print_header(text: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {text}")
    print(f"{'='*60}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Traceable Research Agent — deep research demo"
    )
    parser.add_argument(
        "--question", "-q",
        type=str,
        default=None,
        help="Research question (default: first preset)",
    )
    parser.add_argument(
        "--preset", "-p",
        type=int,
        default=1,
        choices=[1, 2, 3],
        help="Preset question number (1-3, default: 1)",
    )
    parser.add_argument(
        "--report-type", "-r",
        type=str,
        default="summary",
        choices=["summary", "detailed_report", "outline_report"],
        help="Report type (default: summary)",
    )
    args = parser.parse_args()

    question = args.question or PRESET_QUESTIONS[args.preset - 1]

    _print_header("Traceable Research Agent — Deep Research Demo")
    print(f"\n  Research Question: {question}")
    print(f"  Report Type: {args.report_type}")
    print()

    overall_start = perf_counter()

    # ── [1/5] Init ──────────────────────────────────────────────────────
    _print_stage("init", "[1/5] Initializing database and tools...")
    try:
        init_db()
        register_default_tools()
    except Exception as exc:
        _print_stage("error", f"Init failed: {exc}")
        sys.exit(1)

    # ── [2/5] Create task + plan ────────────────────────────────────────
    _print_stage("plan", "[2/5] Creating task and generating plan...")
    with SessionLocal() as db:
        run = store.create_agent_run(
            db=db,
            task=question,
            report_type=args.report_type,
            source_mode="mock",
            allowed_tools=[
                "file_reader", "sql_query",
                "tavily_search", "web_fetcher", "report_writer",
                "arxiv_search", "semantic_scholar_search",
            ],
        )
        plan = plan_task(
            run.task,
            run.allowed_tools_json and json.loads(run.allowed_tools_json) or [],
            "mock",
            planner_mode="deterministic",
        )
        run = store.update_agent_run_plan(db, run.run_id, plan)
        plan_steps = plan.get("steps") or []
        _print_stage("plan", f"  Run ID: {run.run_id}")
        _print_stage("plan", f"  Total steps: {len(plan_steps)}")
        for step in plan_steps:
            print(f"      Step {step.get('step_no')}: {step.get('tool_name')} — {step.get('goal')}")

        # ── [3/5] Execute ───────────────────────────────────────────────
        _print_stage("execute", "[3/5] Executing plan steps...")
        exec_start = perf_counter()
        summary = run_plan(db, run.run_id)
        exec_elapsed = perf_counter() - exec_start

        # ── [4/5] Report ────────────────────────────────────────────────
        _print_stage("report", "[4/5] Generating report...")
        run_after = store.get_agent_run(db, run.run_id)
        if run_after and run_after.report_path:
            report_path = ROOT / run_after.report_path
            if report_path.exists():
                report_text = report_path.read_text(encoding="utf-8")
                _print_stage("report", f"  Report path: {run_after.report_path}")
                _print_stage("report", f"  Report size: {len(report_text)} chars")
                _print_stage("report", "  Preview (first 10 lines):")
                for line in report_text.split("\n")[:10]:
                    if line.strip():
                        print(f"      {line[:100]}")
            else:
                _print_stage("error", f"  Report file not found: {report_path}")

        # ── [5/5] Trace ─────────────────────────────────────────────────
        _print_stage("trace", "[5/5] Trace summary...")
        traces = store.list_tool_traces(db, run.run_id)
        success_count = sum(1 for t in traces if t.status == "success")
        failed_count = sum(1 for t in traces if t.status in ("failed", "rejected"))
        total_token_in = sum(t.token_in or 0 for t in traces)
        total_token_out = sum(t.token_out or 0 for t in traces)
        total_cost = sum(t.estimated_cost or 0 for t in traces)

        _print_stage("trace", f"  Total traces: {len(traces)}")
        _print_stage("trace", f"  Successful: {success_count}")
        if failed_count:
            _print_stage("error", f"  Failed: {failed_count}")
        for t in traces:
            status_icon = "✅" if t.status == "success" else "❌"
            print(f"      {status_icon} Step {t.step_no}: {t.tool_name} ({t.status})")
            if t.output_summary:
                print(f"         {t.output_summary[:120]}")
        if total_token_in or total_token_out:
            _print_stage("trace", f"  LLM tokens: {total_token_in} in / {total_token_out} out")
        if total_cost > 0:
            _print_stage("trace", f"  Estimated LLM cost: ¥{total_cost:.6f}")

    overall_elapsed = perf_counter() - overall_start

    # ── Summary ─────────────────────────────────────────────────────────
    _print_header("Demo Complete")
    print(f"  Status: {summary.get('status', 'unknown')}")
    print(f"  Execution time: {exec_elapsed:.2f}s")
    print(f"  Total time: {overall_elapsed:.2f}s")
    print(f"  Traces: {len(traces)} ({success_count} success, {failed_count} failed)")
    print(f"  Report type: {args.report_type}")
    if total_token_in or total_token_out:
        print(f"  LLM tokens: {total_token_in} in / {total_token_out} out")
        if total_cost > 0:
            print(f"  Est. cost: ¥{total_cost:.6f}")
    print()


if __name__ == "__main__":
    main()
