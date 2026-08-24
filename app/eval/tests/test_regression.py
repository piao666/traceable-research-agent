"""Contracts for the versioned evaluation bad-case registry."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.eval.regression import check_bad_cases, load_bad_cases
from app.eval.run_eval import load_cases


def test_default_bad_case_registry_references_known_eval_cases() -> None:
    known_case_ids = {str(case["case_id"]) for case in load_cases()}

    entries = load_bad_cases(known_case_ids=known_case_ids)

    assert len(entries) == 8
    assert {entry["case_id"] for entry in entries} <= known_case_ids


def test_bad_case_registry_must_exist(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="Bad-case registry not found"):
        load_bad_cases(tmp_path / "missing.json")


def test_bad_case_registry_rejects_unknown_case_id(tmp_path: Path) -> None:
    path = tmp_path / "bad_cases.json"
    path.write_text(
        json.dumps(
            [
                {
                    "id": "BAD-001",
                    "title": "unknown case",
                    "case_id": "not_registered",
                    "status": "known",
                    "priority": "low",
                }
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Unknown eval case_id"):
        load_bad_cases(path, known_case_ids={"registered"})


def test_bad_case_check_only_reports_evaluated_expected_cases() -> None:
    bad_cases = [
        {
            "id": "BAD-001",
            "title": "still failing",
            "case_id": "case-fail",
            "status": "expected_to_pass",
            "priority": "high",
        },
        {
            "id": "BAD-002",
            "title": "now passing",
            "case_id": "case-pass",
            "status": "expected_to_pass",
            "priority": "medium",
        },
        {
            "id": "BAD-003",
            "title": "not evaluated",
            "case_id": "case-filtered-out",
            "status": "expected_to_pass",
            "priority": "low",
        },
    ]

    alerts = check_bad_cases(
        [
            {"case_id": "case-fail", "passed": False},
            {"case_id": "case-pass", "passed": True},
        ],
        bad_cases,
    )

    assert [(alert["case_id"], alert["actual"]) for alert in alerts] == [
        ("case-fail", "still_failing"),
        ("case-pass", "now_passing"),
    ]