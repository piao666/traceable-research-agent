"""Lightweight structural and subset smoke for the Day34 evaluation."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import init_db
from app.eval.fake_react_llm import FakeReActLLMClient, validate_fake_decisions
from app.eval.react_vs_planned import load_cases, run_evaluation
from app.rag.build_index import build_local_index
from app.tools.defaults import register_default_tools
from scripts.init_demo_db import init_demo_db


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    cases = load_cases()
    assert_true(len(cases) >= 15, "Day34 case set is too small")
    decisions = cases[0].get("react_decisions") or []
    assert_true(validate_fake_decisions(decisions), "fake decision shape is invalid")
    assert_true(FakeReActLLMClient(decisions).is_available(), "fake client is unavailable")

    init_db()
    register_default_tools()
    init_demo_db()
    build_local_index()
    subset = cases[:5]
    output = ROOT / "workspace" / "eval_outputs" / "react_vs_planned_smoke.json"
    report = ROOT / "workspace" / "eval_outputs" / "react_vs_planned_smoke.md"
    payload = run_evaluation(subset, output_path=output, report_path=report)
    assert_true(payload["total_cases"] == 5, "subset size mismatch")
    assert_true(set(payload["modes"]) == {"planned", "react"}, "mode metrics missing")
    assert_true(output.exists(), "smoke JSON was not generated")
    assert_true(report.exists() and "## Summary Table" in report.read_text(encoding="utf-8"), "smoke report is invalid")

    print(
        json.dumps(
            {
                "react_vs_planned_eval": "ok",
                "cases": len(cases),
                "subset_cases": len(subset),
                "metrics": "ok",
                "report": "ok",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
