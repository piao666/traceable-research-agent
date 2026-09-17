from app.eval.react_vs_planned import compare_mode_summaries, summarize_mode


def _result(**overrides):
    result = {
        "task_completed": True,
        "report_exists": True,
        "steps": 2,
        "recovered": False,
        "expected_recovery": False,
        "trace_quality_score": 4.0,
        "latency_ms": 10.0,
        "fallback_count": 0,
        "hitl_required": False,
        "hitl_success": None,
        "failure_signal": False,
        "logical_llm_calls": 1,
        "provider_attempts": 1,
        "tool_calls": 2,
        "fetch_attempts": 1,
        "accounted_tokens": 50,
        "budget_overrun": False,
    }
    result.update(overrides)
    return result


def test_p4_summary_reports_call_and_false_completion_counters():
    summary = summarize_mode([
        _result(),
        _result(task_completed=True, failure_signal=True, recovered=False),
    ])
    assert summary["logical_llm_calls"] == 2
    assert summary["provider_attempts"] == 2
    assert summary["tool_calls"] == 4
    assert summary["fetch_attempts"] == 2
    assert summary["false_completion_count"] == 1
    assert summary["budget_overrun_count"] == 0


def test_p4_quality_gate_fails_on_any_unapproved_regression():
    baseline = summarize_mode([_result(), _result()])
    candidate = summarize_mode([
        _result(task_completed=False, report_exists=False, trace_quality_score=3.0),
        _result(),
    ])
    gate = compare_mode_summaries(baseline, candidate)
    assert gate["passed"] is False
    assert {item["metric"] for item in gate["failures"]} == {
        "task_completion_rate",
        "report_exists_rate",
        "trace_quality_score",
    }


def test_p4_quality_gate_does_not_divide_zero_call_baseline():
    baseline = summarize_mode([_result(logical_llm_calls=0, provider_attempts=0)])
    candidate = summarize_mode([_result(logical_llm_calls=1, provider_attempts=2)])
    gate = compare_mode_summaries(baseline, candidate)
    assert gate["zero_call_baseline"] is True
    assert gate["logical_llm_call_reduction_rate"] is None


def test_p4_quality_gate_rejects_new_false_completion_signal():
    baseline = summarize_mode([_result()])
    candidate = summarize_mode([_result(failure_signal=True, recovered=False)])
    gate = compare_mode_summaries(baseline, candidate)
    assert gate["passed"] is False
    assert any(item["metric"] == "false_completion_count" for item in gate["failures"])
