from unittest.mock import patch

from app.agent.dispatcher import run_task_by_mode
from app.eval.fake_react_llm import FakeReActLLMClient

from .conftest import create_root


def test_deep_dispatcher_calls_engine_v2_directly(db, r12_settings):
    root = create_root(db)
    expected = {
        "run_id": root.run_id,
        "status": "running",
        "current_step": 0,
        "total_steps": 0,
        "total_tool_calls": 0,
        "report_url": f"/api/reports/{root.run_id}",
        "trace_url": f"/api/tasks/{root.run_id}/trace",
        "error_message": None,
        "message": "fixture",
    }
    client = FakeReActLLMClient([])
    with patch(
        "app.research.orchestrator.run_deep_research_v2", return_value=expected
    ) as engine:
        run_task_by_mode(db, root.run_id, r12_settings, client)
    engine.assert_called_once()
