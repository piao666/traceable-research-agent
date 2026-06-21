"""Offline smoke for the real chunk-experiment switch and expanded corpus."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings
from scripts.run_rag_chunk_experiment import (
    _build_embedding_backend,
    load_experiment_cases,
    real_embedding_requested,
    run_experiment,
)


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    assert_true(not real_embedding_requested("false"), "false switch was not recognized")
    assert_true(real_embedding_requested("true"), "true switch was not recognized")

    cases = load_experiment_cases()
    assert_true(len(cases) >= 15, "insufficient chunk experiment cases")
    assert_true(len({case["case_id"] for case in cases}) == len(cases), "duplicate case_id")

    docs = sorted((ROOT / "workspace" / "docs").glob("*.md"))
    long_doc = ROOT / "workspace" / "docs" / "optional_long_mixed_document.md"
    assert_true(len(docs) >= 5, "expanded RAG corpus is too small")
    assert_true(long_doc.exists() and len(long_doc.read_text(encoding="utf-8")) > 3000, "long mixed document is missing or too short")

    output = ROOT / "workspace" / "eval_outputs" / "rag_chunk_switch_smoke.json"
    markdown = ROOT / "workspace" / "eval_outputs" / "rag_chunk_switch_smoke.md"
    payload = run_experiment(
        output_path=output,
        markdown_path=markdown,
        settings_obj=Settings(rag_chunk_experiment_sizes="256,512,1024"),
        use_real_embedding=False,
    )
    assert_true(payload["use_real_embedding"] is False, "deterministic flag mismatch")
    assert_true(payload["embedding_backend"] == "deterministic", "default backend is not deterministic")
    assert_true(payload["total_documents"] >= 5, "payload document count is too small")
    assert_true(payload["total_queries"] >= 15, "payload query count is too small")
    assert_true([result["chunk_size"] for result in payload["results"]] == [256, 512, 1024], "chunk sizes mismatch")

    unavailable = Settings(
        rag_embedding_backend="sentence_transformers",
        rag_real_backend_enabled=True,
        rag_model_path=str(ROOT / "workspace" / "tmp" / "missing-model"),
    )
    try:
        _build_embedding_backend(True, unavailable)
    except RuntimeError as exc:
        message = str(exc)
        assert_true("Real embedding backend unavailable" in message, "real error is unclear")
    else:
        raise AssertionError("real backend silently fell back instead of failing")

    print(
        json.dumps(
            {
                "rag_chunk_real_switch": "ok",
                "deterministic_path": "ok",
                "real_switch_detected": "ok",
                "real_unavailable_error": "ok",
                "corpus_docs": len(docs),
                "cases": len(cases),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
