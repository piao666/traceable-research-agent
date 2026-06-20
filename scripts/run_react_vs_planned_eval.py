"""Run the full offline ReAct versus planned evaluation."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import init_db
from app.eval.react_vs_planned import run_evaluation
from app.rag.build_index import build_local_index
from app.tools.defaults import register_default_tools
from scripts.init_demo_db import init_demo_db


def main() -> None:
    init_db()
    register_default_tools()
    init_demo_db()
    build_local_index()
    payload = run_evaluation()
    print(
        json.dumps(
            {
                "react_vs_planned_eval": payload["react_vs_planned_eval"],
                "total_cases": payload["total_cases"],
                "decision_source": payload["decision_source"],
                "modes": payload["modes"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
