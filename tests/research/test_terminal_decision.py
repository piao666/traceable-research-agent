from __future__ import annotations

import json

from app.agent.outcome import finalize_terminal_decision
from app.agent.reporter import save_report
from app.trace import store

from .conftest import create_root


def _plan(root, *, quick: bool = False) -> dict:
    plan = json.loads(root.plan_json or "{}")
    plan["research_mode"] = "quick" if quick else "deep"
    plan["task_contract"] = {
        "goal_kind": "discovery" if quick else "research",
        "unresolved_fields": [],
    }
    plan["research_outcome"] = {
        "version": "research-integrity-v2",
        "status": "passed",
        "effective_evidence_count": 1,
    }
    plan["report_integrity"] = {
        "version": "report-integrity-v2",
        "status": "passed",
        "metrics": {"claim_total": 1, "claim_without_citation": 0},
    }
    return plan


def test_zero_supported_substantive_run_is_incomplete_and_bound_to_report(db, tmp_path):
    root = create_root(db)
    report_path = save_report(root.run_id, "## 3. 最终回答\n\nA claim without support.")
    root = store.update_agent_run_report(db, root.run_id, report_path)
    plan = _plan(root)
    decision = finalize_terminal_decision(db, root, plan, traces=[])
    refreshed = store.get_fresh_agent_run(db, root.run_id)
    assert decision["status"] == "incomplete"
    assert decision["error_code"] in {"report_integrity_missing", "evidence_snapshot_missing"}
    assert decision["report_sha256"]
    assert decision["evidence_snapshot_id"]
    assert refreshed.status == "incomplete"


def test_unverified_quick_discovery_cannot_complete_without_safe_report(db):
    root = create_root(db)
    report_path = save_report(root.run_id, "## 3. 最终回答\n\n发现来源列表。")
    root = store.update_agent_run_report(db, root.run_id, report_path)
    plan = _plan(root, quick=True)
    decision = finalize_terminal_decision(db, root, plan, traces=[])
    assert decision["status"] == "incomplete"
    assert store.get_fresh_agent_run(db, root.run_id).status == "incomplete"


def test_terminal_decision_does_not_mutate_cancelled_run(db):
    root = create_root(db)
    store.update_agent_run_status(db, root.run_id, "cancelled", "cancelled")
    decision = finalize_terminal_decision(db, root, _plan(root), traces=[])
    assert decision["error_code"] == "run_not_finalizable"
    assert store.get_fresh_agent_run(db, root.run_id).status == "cancelled"
