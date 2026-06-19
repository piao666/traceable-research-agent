"""Static smoke checks for the Streamlit frontend demo layer."""

from __future__ import annotations

import json
import py_compile
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND_APP = ROOT / "frontend" / "streamlit_app.py"
FRONTEND_README = ROOT / "frontend" / "README.md"
REQUIREMENTS = ROOT / "requirements.txt"


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    assert_true(FRONTEND_APP.exists(), "frontend/streamlit_app.py missing")
    assert_true(FRONTEND_README.exists(), "frontend/README.md missing")
    assert_true(REQUIREMENTS.exists(), "requirements.txt missing")

    requirements = REQUIREMENTS.read_text(encoding="utf-8").lower()
    assert_true("streamlit" in requirements, "streamlit missing from requirements.txt")
    assert_true("requests" in requirements, "requests missing from requirements.txt")

    py_compile.compile(str(FRONTEND_APP), doraise=True)
    source = FRONTEND_APP.read_text(encoding="utf-8")

    required_paths = [
        "/health",
        "/api/tasks",
        "/plan",
        "/run",
        "/run_async",
        "/trace",
        "/api/reports",
        "/confirm",
    ]
    missing_paths = [path for path in required_paths if path not in source]
    assert_true(not missing_paths, f"missing API paths: {missing_paths}")

    metadata_fields = [
        "embedding_backend",
        "vector_backend",
        "fallback_used",
        "dimension",
        "collection_name",
    ]
    missing_metadata = [field for field in metadata_fields if field not in source]
    assert_true(not missing_metadata, f"missing RAG metadata display fields: {missing_metadata}")
    assert_true("Trace details:" in source, "trace details expander missing")
    for control in ["API Key", "Tenant ID", "User ID", "Use async run", "Execution Mode"]:
        assert_true(control in source, f"missing Streamlit control: {control}")
    assert_true('type="password"' in source, "API key input is not password protected")
    for react_field in ["Thought", "Action", "Observation", "ReAct Trace"]:
        assert_true(react_field in source, f"missing ReAct trace display: {react_field}")

    forbidden_patterns = [
        r"QWEN_API_KEY\s*=",
        r"DEEPSEEK_API_KEY\s*=",
        r"sk-[A-Za-z0-9_\-]{16,}",
        r"Bearer\s+[A-Za-z0-9_\-]{20,}",
    ]
    hits = []
    for pattern in forbidden_patterns:
        if re.search(pattern, source):
            hits.append(pattern)
    assert_true(not hits, f"potential hardcoded secret patterns found: {hits}")

    print(
        json.dumps(
            {
                "streamlit_frontend": "ok",
                "files": "ok",
                "requirements": "ok",
                "api_paths": "ok",
                "rag_metadata_display": "ok",
                "secret_scan": "ok",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
