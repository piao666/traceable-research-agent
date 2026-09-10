from unittest.mock import patch

import pytest

from app.agent.deepening import run_deepening

from .conftest import create_root


def test_legacy_deepening_is_warning_only_v2_adapter(db, r12_settings):
    root = create_root(db)
    expected = {"run_id": root.run_id, "status": "completed"}
    with patch(
        "app.research.orchestrator.run_deep_research_v2", return_value=expected
    ) as engine, pytest.warns(DeprecationWarning):
        result = run_deepening(db, root.run_id, r12_settings)
    assert result == expected
    engine.assert_called_once()
