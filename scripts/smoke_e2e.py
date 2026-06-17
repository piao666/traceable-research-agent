"""Internal smoke for create -> plan -> run -> trace -> report."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agent.executor import run_plan
from app.agent.planner import plan_task
from app.database import SessionLocal, init_db
from app.tools.defaults import register_default_tools
from app.trace import store


def main() -> None:
    init_db()
    register_default_tools()
    with SessionLocal() as db:
        run = store.create_agent_run(
            db=db,
            task="Read local docs, query database metrics, retrieve trace evidence, and generate a markdown report",
            report_type="summary",
            source_mode="mock",
            allowed_tools=["file_reader", "sql_query", "rag_search", "report_writer"],
        )
        plan = plan_task(run.task, ["file_reader", "sql_query", "rag_search", "report_writer"], "mock")
        run = store.update_agent_run_plan(db, run.run_id, plan)
        if run.status != "pending" or run.total_steps != 4:
            raise SystemExit(f"Unexpected create/plan state: {run.status}, steps={run.total_steps}")
        if store.list_tool_traces(db, run.run_id):
            raise SystemExit("Trace should be empty before manual run.")

        summary = run_plan(db, run.run_id)
        traces = store.list_tool_traces(db, run.run_id)
        if summary["status"] != "completed" or len(traces) < 3:
            raise SystemExit(f"Unexpected run result: {summary}, traces={len(traces)}")
        report_path = ROOT / str(store.get_agent_run(db, run.run_id).report_path)
        if not report_path.exists():
            raise SystemExit(f"Report was not generated: {report_path}")

        before = len(traces)
        repeated = run_plan(db, run.run_id)
        after = len(store.list_tool_traces(db, run.run_id))
        if before != after:
            raise SystemExit("Repeated completed run wrote duplicate traces.")

        print(
            {
                "e2e": "ok",
                "run_id": run.run_id,
                "status": summary["status"],
                "trace_count": len(traces),
                "repeated_message": repeated["message"],
                "report_path": str(report_path.relative_to(ROOT)),
            }
        )


if __name__ == "__main__":
    main()
