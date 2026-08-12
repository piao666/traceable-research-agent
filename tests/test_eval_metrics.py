"""Focused tests for declarative evaluation metric assertions."""

from __future__ import annotations

import unittest

from app.eval.run_eval import evaluate_metric_assertions


class EvalMetricAssertionTests(unittest.TestCase):
    def test_nested_paths_and_list_indexes(self) -> None:
        passed, results = evaluate_metric_assertions(
            {"output": {"pages": [{"error": "validation failed"}]}},
            [{"path": "output.pages[0].error", "equals": "validation failed"}],
        )

        self.assertTrue(passed)
        self.assertTrue(results[0]["passed"])

    def test_supported_operators(self) -> None:
        payload = {
            "output": {
                "value": 5,
                "name": "traceable research",
                "items": ["alpha", "beta"],
                "metadata": {"tier": "T0"},
            }
        }
        assertions = [
            {"path": "output.value", "exists": True},
            {"path": "output.value", "equals": 5},
            {"path": "output.value", "not_equals": 4},
            {"path": "output.value", "min": 4},
            {"path": "output.value", "max": 6},
            {"path": "output.name", "contains": "research"},
            {"path": "output.items", "contains": "beta"},
            {"path": "output.metadata", "contains": "tier"},
            {"path": "output.items", "length": 2},
            {"path": "output.missing", "exists": False},
        ]

        passed, results = evaluate_metric_assertions(payload, assertions)

        self.assertTrue(passed)
        self.assertTrue(all(result["passed"] for result in results))

    def test_missing_path_fails_value_assertion(self) -> None:
        passed, results = evaluate_metric_assertions(
            {"output": {}},
            [{"path": "output.missing", "equals": "value"}],
        )

        self.assertFalse(passed)
        self.assertFalse(results[0]["passed"])
        self.assertIsNone(results[0]["actual"])

    def test_cases_without_metric_assertions_remain_compatible(self) -> None:
        passed, results = evaluate_metric_assertions({"output": {}}, None)

        self.assertTrue(passed)
        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
