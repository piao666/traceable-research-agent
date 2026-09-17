from app.agent.parallel_executor import (
    _finish_parallel_operation,
    recover_parallel_operations,
    _reserve_parallel_operation,
)
from app.research.models import ResearchOperation
from app.tools.base import ToolResult

from .conftest import create_root


def test_parallel_operation_journal_marks_interrupted_attempt_and_retries(db):
    root = create_root(db)
    step = {"step_no": 3, "tool_name": "tavily_search", "arguments": {"query": "pear"}}

    first = _reserve_parallel_operation(db, root.run_id, step)
    assert first.attempt == 1
    assert first.status == "running"
    _finish_parallel_operation(
        db,
        first.operation_id,
        ToolResult(success=False, error_message="late timeout"),
        interrupted=True,
    )
    second = _reserve_parallel_operation(db, root.run_id, step)

    assert second.attempt == 2
    assert second.status == "running"
    assert db.get(ResearchOperation, first.operation_id).status == "interrupted"


def test_parallel_recovery_marks_running_leases_unknown(db):
    root = create_root(db)
    operation = _reserve_parallel_operation(
        db,
        root.run_id,
        {"step_no": 4, "tool_name": "tavily_search", "arguments": {"query": "restart"}},
    )

    assert recover_parallel_operations(db, root.run_id) == 1
    recovered = db.get(ResearchOperation, operation.operation_id)
    assert recovered.status == "interrupted"
    assert "unknown" in recovered.error_message
