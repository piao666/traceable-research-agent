"""Docker startup entrypoint for the lightweight demo container."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def _is_enabled(value: str | None, default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _run(command: list[str]) -> None:
    print(f"[docker-entrypoint] running: {' '.join(command)}", flush=True)
    subprocess.run(command, check=True)


def _rag_index_is_ready() -> bool:
    vector_backend = os.getenv("RAG_VECTOR_BACKEND", "json").strip().lower()
    if vector_backend == "chroma":
        chroma_dir = Path(os.getenv("RAG_CHROMA_DIR", "workspace/chroma"))
        dense_index = chroma_dir / "chroma.sqlite3"
    else:
        dense_index = Path("workspace/index/rag_index.json")
    bm25_index = Path("workspace/index/bm25_index.json")
    return dense_index.is_file() and bm25_index.is_file()


def _should_build_rag_index() -> bool:
    if _is_enabled(os.getenv("DOCKER_REBUILD_RAG_INDEX"), default=False):
        return True
    return not _rag_index_is_ready()


def main() -> None:
    _run([sys.executable, "scripts/migrate_database.py"])
    if _is_enabled(os.getenv("DOCKER_INIT_DEMO_DATA"), default=True):
        _run([sys.executable, "scripts/init_demo_db.py"])
        if _should_build_rag_index():
            _run([sys.executable, "scripts/build_rag_index.py"])
        else:
            print(
                "[docker-entrypoint] reusing persisted RAG index; set "
                "DOCKER_REBUILD_RAG_INDEX=true to rebuild.",
                flush=True,
            )
    else:
        print("[docker-entrypoint] DOCKER_INIT_DEMO_DATA=false; skipping demo init.", flush=True)

    _run(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
        ]
    )


if __name__ == "__main__":
    main()
