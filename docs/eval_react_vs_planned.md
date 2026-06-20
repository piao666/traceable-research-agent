# ReAct vs Planned Quantitative Evaluation

## Purpose

The planned executor is the stable sequential baseline. The optional ReAct executor chooses each next action from Thought/Action/Observation state. This evaluation compares completion, recovery, trace structure, and latency without adding new production behavior.

## Evaluation Setup

* total cases: `18`
* modes: `planned`, `react`
* decision source: `fake_deterministic`
* tools: `file_reader`, `sql_query`, `rag_search`, `mcp_github_search`, `report_writer`
* data: repository demo documents, demo SQLite data, deterministic RAG, and offline GitHub mock/fallback
* runtime: local Python process with the existing executors and Tool Registry

## Metrics

* `task_completion_rate`: completed run, report present, and at least one configured success keyword found.
* `report_exists_rate`: runs with a persisted Markdown report.
* `avg_steps`: average persisted trace rows.
* `recovery_count`: expected failure scenarios that produced a report after a failure, rejection, empty result, fallback, or bounded limitation.
* `failed_tool_recovery_rate`: recovered expected-recovery cases divided by all expected-recovery cases.
* `trace_quality_score`: deterministic 1-5 structural score; planned is capped at 4 and ReAct can reach 5 when recovery/limitation is explicit.
* `avg_latency_ms`: local wall-clock execution time per case, including HITL resume inside the harness.

## Summary Table

| Mode | Completion | Report Exists | Avg Steps | Recovery | Failed Recovery | Trace Quality | Avg Latency |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Planned | 100.0% | 100.0% | 1.278 | 1 | 14.3% | 3.889 | 826.514 ms |
| ReAct | 100.0% | 100.0% | 2.611 | 6 | 85.7% | 4.278 | 1299.416 ms |

## Scenario Breakdown

Result cells show `status / trace quality`.

| Scenario | Planned Result | ReAct Result | Notes |
| --- | --- | --- | --- |
| normal_file_report | completed / 4.0 | completed / 4.0 | Planned was faster in this local run. |
| normal_rag_report | completed / 4.0 | completed / 4.0 | Planned was faster in this local run. |
| sql_metadata_query | completed / 4.0 | completed / 4.0 | Planned was faster in this local run. |
| github_mock_report | completed / 4.0 | completed / 4.0 | Planned was faster in this local run. |
| normal_file_rag_report | completed / 4.0 | completed / 4.0 | Planned was faster in this local run. |
| hybrid_rag_report | completed / 4.0 | completed / 4.0 | Planned was faster in this local run. |
| bm25_rag_report | completed / 4.0 | completed / 4.0 | Planned was faster in this local run. |
| rag_no_hit_recovery | completed / 4.0 | completed / 5.0 | ReAct adapted after the observed failure. Planned was faster in this local run. |
| sql_rejected_recovery | completed / 3.0 | completed / 5.0 | ReAct adapted after the observed failure. Planned was faster in this local run. |
| github_fallback_recovery | completed / 4.0 | completed / 5.0 | Planned was faster in this local run. |
| tool_failure_recovery | completed / 3.0 | completed / 5.0 | ReAct adapted after the observed failure. Planned was faster in this local run. |
| hitl_report | completed / 4.0 | completed / 4.0 | Planned was faster in this local run. |
| repeated_tool_limit | completed / 4.0 | completed / 5.0 | ReAct adapted after the observed failure. Planned was faster in this local run. ReAct ended with a bounded limitation. |
| max_steps_limit | completed / 4.0 | completed / 4.0 | Planned was faster in this local run. ReAct ended with a bounded limitation. |
| invalid_decision_fallback | completed / 4.0 | completed / 4.0 | ReAct adapted after the observed failure. Planned was faster in this local run. |
| mixed_tools_report | completed / 4.0 | completed / 4.0 | Planned was faster in this local run. |
| sql_with_report | completed / 4.0 | completed / 4.0 | Planned was faster in this local run. |
| restricted_tools_limitation | completed / 4.0 | completed / 4.0 | Comparable outcome. |

## Key Findings

* ReAct recovered `6` expected failure scenarios versus `1` for planned execution.
* ReAct trace quality averaged `4.278` versus `3.889` because decision rationale and observations are persisted.
* Planned averaged `1.278` steps and `826.514` ms; ReAct averaged `2.611` steps and `1299.416` ms. The dynamic loop can trade a longer path for recovery context.
* Results are reported as measured; this harness does not force ReAct to outperform the baseline.

## Limitations

* The default run uses a fake/mock deterministic ReAct decision policy for reproducibility.
* Real Qwen/DeepSeek evaluation requires `RUN_REACT_REAL_LLM_EVAL=true` and a locally configured provider key.
* The case set is intentionally small and is not a large-scale benchmark.
* Trace quality is a rule-based structural score, not a blinded human rating.
* Local millisecond latency is useful for regression comparison but not provider-scale performance modeling.

## Next Step

Extend optional real-LLM evaluation, add more domain tasks, and introduce human trace-quality review before treating the results as a broader benchmark.
