"""Small state helpers for agent execution."""

from typing import Any

Observation = dict[str, Any]

# ── Phase 7.4: Plan approval state ───────────────────────────────────
WAITING_HUMAN_PLAN = "waiting_human_plan"
