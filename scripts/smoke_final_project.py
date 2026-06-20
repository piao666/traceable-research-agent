"""Aggregate the stable lightweight smoke/eval suite for final packaging."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from time import perf_counter


ROOT = Path(__file__).resolve().parents[1]
CHECKS = [
    ("smoke_planner", [sys.executable, "scripts/smoke_planner.py"]),
    ("smoke_e2e", [sys.executable, "scripts/smoke_e2e.py"]),
    ("smoke_exceptions", [sys.executable, "scripts/smoke_exceptions.py"]),
    ("smoke_hitl", [sys.executable, "scripts/smoke_hitl.py"]),
    ("smoke_llm_config", [sys.executable, "scripts/smoke_llm_config.py"]),
    ("smoke_llm_planner", [sys.executable, "scripts/smoke_llm_planner.py"]),
    ("smoke_rag_query", [sys.executable, "scripts/smoke_rag_query.py"]),
    ("smoke_rag_backends", [sys.executable, "scripts/smoke_rag_backends.py"]),
    ("smoke_streamlit_frontend", [sys.executable, "scripts/smoke_streamlit_frontend.py"]),
    ("smoke_auth_async", [sys.executable, "scripts/smoke_auth_async.py"]),
    ("smoke_alembic_sql_parser", [sys.executable, "scripts/smoke_alembic_sql_parser.py"]),
    ("smoke_github_mcp", [sys.executable, "scripts/smoke_github_mcp.py"]),
    ("smoke_react_executor", [sys.executable, "scripts/smoke_react_executor.py"]),
    ("smoke_hybrid_rag", [sys.executable, "scripts/smoke_hybrid_rag.py"]),
    ("smoke_react_vs_planned_eval", [sys.executable, "scripts/smoke_react_vs_planned_eval.py"]),
    ("app_eval", [sys.executable, "-m", "app.eval.run_eval"]),
]


def main() -> None:
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    results: list[dict[str, object]] = []

    for name, command in CHECKS:
        started = perf_counter()
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            check=False,
        )
        duration_ms = round((perf_counter() - started) * 1000, 3)
        result = {
            "name": name,
            "status": "passed" if completed.returncode == 0 else "failed",
            "return_code": completed.returncode,
            "duration_ms": duration_ms,
        }
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        if completed.returncode != 0:
            summary = {
                "final_project_smoke": "failed",
                "passed_scripts": sum(item["status"] == "passed" for item in results),
                "failed_scripts": 1,
                "failed_check": name,
                "eval": "not_run" if name != "app_eval" else "failed",
            }
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            raise SystemExit(completed.returncode or 1)

    summary = {
        "final_project_smoke": "ok",
        "passed_scripts": len(results),
        "failed_scripts": 0,
        "eval": "passed",
        "checks": results,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
