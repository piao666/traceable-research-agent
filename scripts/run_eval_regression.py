"""Regression evaluation runner — thin wrapper.

All logic lives in app/eval/regression.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.eval.regression import main

if __name__ == "__main__":
    raise SystemExit(main())