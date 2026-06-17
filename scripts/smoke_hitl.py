"""Internal smoke for minimal human-in-the-loop planning and resume."""

from datetime import datetime, timezone
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
        task = "Read local docs, retrieve trace evidence, and generate a markdown report with human approval"
        run = store.create_agent_run(
            db=db,
            task=task,
            report_type="summary",
            source_mode="mock",
            allowed_tools=["file_reader", "rag_search", "report_writer"],
        )
        plan = plan_task(task, ["file_reader", "rag_search", "report_writer"], "mock")
        report_steps = [step for step in plan["steps"] if step["tool_name"] == "report_writer"]
        if not report_steps or not report_steps[0]["requires_confirmation"]:
            raise SystemExit("Planner did not mark report_writer as requiring confirmation.")

        run = store.update_agent_run_plan(db, run.run_id, plan)
        waiting = run_plan(db, run.run_id)
        if waiting["status"] != "waiting_human":
            raise SystemExit(f"Expected waiting_human, got {waiting}")

        plan["confirmation"] = {
            "required_step_no": report_steps[0]["step_no"],
            "required_tool_name": "report_writer",
            "approved": True,
            "comment": "Approved by smoke_hitl.",
            "approved_at": datetime.now(timezone.utc).isoformat(),
        }
        store.replace_agent_run_plan(db, run.run_id, plan)
        completed = run_plan(db, run.run_id)
        if completed["status"] != "completed":
            raise SystemExit(f"Expected completed after confirmation, got {completed}")

        final_run = store.get_agent_run(db, run.run_id)
        report_path = ROOT / str(final_run.report_path)
        markdown = report_path.read_text(encoding="utf-8")
        if "Human Confirmation" not in markdown:
            raise SystemExit("Report is missing Human Confirmation section.")

        print(
            {
                "hitl": "ok",
                "run_id": run.run_id,
                "waiting_status": waiting["status"],
                "completed_status": completed["status"],
                "report_path": str(report_path.relative_to(ROOT)),
            }
        )


if __name__ == "__main__":
    main()
