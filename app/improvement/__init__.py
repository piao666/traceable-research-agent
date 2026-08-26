"""Self-improvement module: auto-evaluation, strategy weighting, few-shot.

Phase 1: Auto-evaluation feedback loop — score every completed run and
         accumulate strategy→effect data for downstream optimization.
"""

from __future__ import annotations