# Evaluation and Smoke Notes

## Layered Validation

Smoke tests focus on stable engineering paths: planner output, end-to-end tool
execution, visible exceptions, HITL, LLM configuration/fallback, RAG backends,
Auth/Async, Alembic, SQL safety, GitHub cache/fallback, ReAct guards, Hybrid
RAG, Streamlit structure, and chunk experiments. The final aggregator invokes
existing scripts instead of duplicating their assertions.

Application eval cases cover successful flows and bad cases. Metrics include
task success, trace completeness, safety hits, and visible failures. Runtime
JSON is written under ignored `workspace/eval_outputs`.

## ReAct versus Planned

The quantitative comparison uses deterministic scripted ReAct decisions by
default so results are reproducible without a provider key. It measures
completion, report existence, avg_steps, recovery_count,
failed_tool_recovery_rate, fallback count, HITL success, latency, and a
rule-based `trace_quality_score`.

Trace quality ranges from 1-5. A missing trace scores lowest. Complete input
and output summaries, ordered tool calls, and a complete plan raise planned
quality up to four. A ReAct trace can reach five when Thought, Action,
Observation, and recovery or a transparent limitation are present.

The measured demo showed equal completion but different trade-offs: planned
execution was shorter and faster, while ReAct recovered more expected failure
cases. The experiment supports mode selection; it does not claim that dynamic
execution should replace the stable baseline.

## Docker and Optional Models

Lightweight Docker validation installs `requirements-docker-light.txt` and
checks build, startup, `/health`, and shutdown without bundling a local model.
Real SentenceTransformers/Chroma validation runs separately in the local Python
environment. This keeps default Docker and CI independent of large model
artifacts while still providing optional real retrieval evidence.

Every reported metric must be calculated from actual runs. Small corpora can
saturate recall, and machine-specific latency varies. Reports must explain
these limitations instead of hardcoding a preferred outcome.
