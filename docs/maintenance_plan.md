# Maintenance Plan

## Current Risk

The demo is feature-complete enough for interview and portfolio use, but several
files now carry more responsibility than ideal:

| File | Current Role | Near-term Risk |
| --- | --- | --- |
| `frontend/streamlit_app.py` | Full Streamlit console | UI changes touch a large single file. |
| `app/agent/planner.py` | Rule planner, templates, tool selection | Adding a new mode may mix prompt, rule, and guardrail logic. |
| `app/agent/reporter.py` | Markdown synthesis and report shaping | More report formats would increase branching. |
| `app/agent/evidence.py` | Evidence extraction, grouping, export context | New evidence types may create long condition chains. |
| `app/config.py` | Flat environment settings | More providers would make the settings object harder to scan. |

## Refactor Boundary

Do not refactor during demo stabilization unless a feature needs it. The safer
sequence is:

1. Add tests around the current behavior before moving code.
2. Extract pure helpers first; keep public API and trace schema unchanged.
3. Move one responsibility at a time.
4. Run `python -m unittest discover -s tests`, selected smoke scripts, and
   `scripts/smoke_final_project.py` after larger moves.

## Suggested Slices

1. Split Streamlit into `frontend/components/`, `frontend/api_client.py`, and
   `frontend/state.py`.
2. Split planner templates and source selection into `app/agent/planner_rules.py`
   and keep `planner.py` as the orchestration entrypoint.
3. Split reporter formatting from evidence selection.
4. Split evidence source classifiers from export/render helpers.
5. Group settings into documented sections or nested models after the demo
   environment variable names are frozen.

## Interview Framing

The honest answer is that the current shape optimized for a time-boxed,
traceable demo. The next engineering step is not another provider integration;
it is reducing change radius with tests and small extraction slices while
preserving the task, trace, evidence, and report contracts.
