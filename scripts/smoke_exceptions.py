"""Internal smoke for tool exception and safety result shapes."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.tools.file_reader import read_file
from app.tools.rag_search import DEFAULT_INDEX_PATH, search_rag
from app.tools.sql_query import run_query
from app.agent.executor import run_plan
from app.database import SessionLocal, init_db
from app.tools.defaults import register_default_tools
from app.trace.logger import record_tool_result
from app.trace import store


def _expect(result, *, success: bool, error_type: str | None = None) -> None:
    if result.success is not success:
        raise SystemExit(f"Unexpected success={result.success}: {result}")
    if error_type is not None and result.metadata.get("error_type") != error_type:
        raise SystemExit(f"Unexpected error_type={result.metadata.get('error_type')}: {result}")


def main() -> None:
    _expect(
        read_file({"path": "missing_file.md", "max_chars": 100}),
        success=False,
        error_type="not_found",
    )
    _expect(
        read_file({"path": "../task.txt", "max_chars": 100}),
        success=False,
        error_type="safety_rejected",
    )
    _expect(
        run_query({"query": "DELETE FROM documents", "limit": 5}),
        success=False,
        error_type="safety_rejected",
    )
    _expect(
        run_query({"query": "SELECT * FROM missing_table", "limit": 5}),
        success=False,
        error_type="sql_error",
    )
    _expect(
        search_rag({"query": "   ", "top_k": 3}),
        success=False,
        error_type="invalid_args",
    )

    backup = DEFAULT_INDEX_PATH.with_suffix(".json.bak")
    restored = False
    if DEFAULT_INDEX_PATH.exists():
        if backup.exists():
            backup.unlink()
        DEFAULT_INDEX_PATH.rename(backup)
        try:
            _expect(
                search_rag({"query": "trace registry", "top_k": 3, "retrieval_mode": "dense"}),
                success=False,
                error_type="index_missing",
            )
            hybrid_fallback = search_rag({"query": "trace registry", "top_k": 3})
            _expect(hybrid_fallback, success=True)
            if hybrid_fallback.metadata.get("requested_retrieval_mode") != "hybrid":
                raise SystemExit(f"Default RAG mode should be hybrid: {hybrid_fallback}")
            if not hybrid_fallback.metadata.get("fallback_used"):
                raise SystemExit(f"Hybrid missing dense index should fallback: {hybrid_fallback}")
        finally:
            backup.rename(DEFAULT_INDEX_PATH)
            restored = True

    init_db()
    register_default_tools()
    with SessionLocal() as db:
        trace_run = store.create_agent_run(
            db,
            task="Trace exception visibility smoke",
            report_type="summary",
            source_mode="mock",
            allowed_tools=["file_reader", "sql_query", "rag_search"],
        )
        traced_results = [
            (
                1,
                "file_reader",
                {"path": "missing_file.md", "max_chars": 100},
                read_file({"path": "missing_file.md", "max_chars": 100}),
            ),
            (
                2,
                "file_reader",
                {"path": "../task.txt", "max_chars": 100},
                read_file({"path": "../task.txt", "max_chars": 100}),
            ),
            (
                3,
                "sql_query",
                {"query": "DELETE FROM documents", "limit": 5},
                run_query({"query": "DELETE FROM documents", "limit": 5}),
            ),
            (
                4,
                "sql_query",
                {"query": "SELECT * FROM missing_table", "limit": 5},
                run_query({"query": "SELECT * FROM missing_table", "limit": 5}),
            ),
            (
                5,
                "rag_search",
                {"query": "   ", "top_k": 3},
                search_rag({"query": "   ", "top_k": 3}),
            ),
        ]
        for step_no, tool_name, arguments, result in traced_results:
            record_tool_result(db, trace_run.run_id, step_no, tool_name, arguments, result, 0)
        statuses = [trace.status for trace in store.list_tool_traces(db, trace_run.run_id)]
        if statuses.count("rejected") < 2 or statuses.count("failed") < 3:
            raise SystemExit(f"Unexpected exception trace statuses: {statuses}")

        unknown_run = store.create_agent_run(
            db,
            task="Run an unknown demo tool",
            report_type="summary",
            source_mode="mock",
            allowed_tools=["unknown_tool"],
        )
        store.update_agent_run_plan(
            db,
            unknown_run.run_id,
            {
                "version": "deterministic-v1",
                "task": unknown_run.task,
                "source_mode": "mock",
                "allowed_tools": ["unknown_tool"],
                "steps": [
                    {
                        "step_no": 1,
                        "goal": "Exercise unknown tool handling.",
                        "tool_name": "unknown_tool",
                        "arguments": {},
                        "expected_output": "Stable failed trace.",
                        "completion_criteria": "Executor does not crash.",
                        "risk_level": "low",
                        "requires_confirmation": False,
                    }
                ],
                "notes": [],
                "confirmation": None,
            },
        )
        summary = run_plan(db, unknown_run.run_id)
        traces = store.list_tool_traces(db, unknown_run.run_id)
        if summary["status"] != "completed" or not traces or traces[0].status != "failed":
            raise SystemExit("Unknown tool handling did not produce completed run with failed trace.")

        empty_run = store.create_agent_run(
            db,
            task="No executable allowed tools",
            report_type="summary",
            source_mode="mock",
            allowed_tools=[],
        )
        store.update_agent_run_plan(
            db,
            empty_run.run_id,
            {
                "version": "deterministic-v1",
                "task": empty_run.task,
                "source_mode": "mock",
                "allowed_tools": [],
                "steps": [],
                "notes": ["No executable planning step due to allowed_tools restriction."],
                "confirmation": None,
            },
        )
        empty_summary = run_plan(db, empty_run.run_id)
        if empty_summary["status"] != "completed":
            raise SystemExit(f"Empty plan did not complete with limitation report: {empty_summary}")

    print(
        {
            "exceptions": "ok",
            "covered": [
                "file_missing",
                "path_traversal",
                "sql_delete_rejected",
                "sql_missing_table",
                "rag_empty_query",
                "rag_index_missing" if restored else "rag_index_missing_skipped_no_index",
                "unknown_tool_failed_trace",
                "empty_plan_limitation_report",
                "exception_trace_statuses",
            ],
        }
    )


if __name__ == "__main__":
    main()
