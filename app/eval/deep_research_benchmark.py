"""Validation for the fixed Deep Research P4 benchmark manifest.

The manifest is intentionally data-only.  Loading it never creates a Run or
contacts a provider; real E2E runners may opt into the explicitly marked
network cases after the same contract has been validated.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASES_PATH = ROOT / "benchmarks" / "deep_research" / "golden_cases.jsonl"
MIN_CASES = 20
REQUIRED_CATEGORIES = frozenset(
    {
        "current_news",
        "github_ecosystem",
        "product_comparison",
        "academic_review",
        "policy",
        "business",
        "conflict_research",
        "js_page",
        "pdf_chart",
        "tabular_data",
        "structured_data",
        "syndication",
        "provider_failure",
        "long_report",
    }
)
RELEASE_GOLDEN_IDS = frozenset(
    {
        "golden_trend",
        "golden_comparison",
        "golden_academic",
        "golden_data_charts",
        "golden_conflict",
    }
)
FIXED_FIELDS = (
    "dataset_version",
    "provider",
    "model",
    "prompt_version",
    "controller_version",
    "policy_version",
)
REQUIRED_ACCEPTANCE_FIELDS = (
    "required_requirement_coverage",
    "critical_requirement_coverage",
    "max_false_completions",
    "max_conflict_leakage",
)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(f"Deep Research benchmark manifest not found: {path}")
    cases: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid benchmark JSON at line {line_no}: {exc.msg}") from exc
        if not isinstance(item, dict):
            raise ValueError(f"Benchmark case at line {line_no} must be an object")
        cases.append(item)
    return cases


def validate_deep_research_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate the reproducibility and release-golden contract."""

    if len(cases) < MIN_CASES:
        raise ValueError(f"Deep Research benchmark requires at least {MIN_CASES} cases")
    seen: set[str] = set()
    for index, case in enumerate(cases, start=1):
        case_id = str(case.get("case_id") or "").strip()
        category = str(case.get("category") or "").strip()
        task = str(case.get("task") or "").strip()
        if not case_id or not category or not task:
            raise ValueError(f"Benchmark case {index} requires case_id, category, and task")
        if case_id in seen:
            raise ValueError(f"Duplicate benchmark case_id: {case_id}")
        seen.add(case_id)
        if category not in REQUIRED_CATEGORIES:
            raise ValueError(f"Unsupported benchmark category for {case_id}: {category}")
        if case.get("source_mode") != "recorded":
            raise ValueError(f"Benchmark case {case_id} must use source_mode=recorded")
        if case.get("expected_controller") != "pear-v2":
            raise ValueError(f"Benchmark case {case_id} must target expected_controller=pear-v2")
        if not isinstance(case.get("network_dependent"), bool):
            raise ValueError(f"Benchmark case {case_id} must declare network_dependent")
        missing = [field for field in FIXED_FIELDS if not str(case.get(field) or "").strip()]
        if missing:
            raise ValueError(f"Benchmark case {case_id} missing fixed fields: {', '.join(missing)}")
        budget = case.get("hard_budget")
        if not isinstance(budget, dict) or not all(
            isinstance(budget.get(key), int) and budget[key] > 0
            for key in ("max_seconds", "max_tool_calls", "max_llm_calls")
        ):
            raise ValueError(f"Benchmark case {case_id} requires positive hard_budget limits")
        acceptance = case.get("acceptance")
        if not isinstance(acceptance, dict):
            raise ValueError(f"Benchmark case {case_id} requires acceptance gates")
        if any(field not in acceptance for field in REQUIRED_ACCEPTANCE_FIELDS):
            raise ValueError(f"Benchmark case {case_id} has incomplete acceptance gates")
        for field in ("required_requirement_coverage", "critical_requirement_coverage"):
            value = acceptance[field]
            if not isinstance(value, (int, float)) or not 0 <= value <= 1:
                raise ValueError(f"Benchmark case {case_id} has invalid {field}")
        for field in ("max_false_completions", "max_conflict_leakage"):
            value = acceptance[field]
            if not isinstance(value, int) or value < 0:
                raise ValueError(f"Benchmark case {case_id} has invalid {field}")

    categories = {str(case["category"]) for case in cases}
    missing_categories = REQUIRED_CATEGORIES - categories
    if missing_categories:
        raise ValueError(
            "Deep Research benchmark is missing categories: "
            + ", ".join(sorted(missing_categories))
        )
    golden_ids = {str(case["case_id"]) for case in cases if case.get("release_golden") is True}
    if golden_ids != RELEASE_GOLDEN_IDS:
        raise ValueError(
            "Release golden set must be exactly: " + ", ".join(sorted(RELEASE_GOLDEN_IDS))
        )
    fixed_values = {
        field: {str(case[field]) for case in cases}
        for field in FIXED_FIELDS
    }
    drift = [field for field, values in fixed_values.items() if len(values) != 1]
    if drift:
        raise ValueError("Benchmark fixed-version drift: " + ", ".join(drift))
    return cases


def load_deep_research_cases(path: Path = DEFAULT_CASES_PATH) -> list[dict[str, Any]]:
    """Load and validate the default or caller-supplied manifest."""

    return validate_deep_research_cases(_load_jsonl(path))
