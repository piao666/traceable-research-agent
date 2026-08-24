"""Smoke for ReAct vs planned evaluation — thin wrapper.

All logic lives in app/eval/smoke/smoke_react_vs_planned_eval.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.eval.smoke.smoke_react_vs_planned_eval import main

if __name__ == "__main__":
    main()