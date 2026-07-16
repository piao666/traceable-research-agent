"""Docker startup entrypoint for the lightweight demo container."""

from __future__ import annotations

import os
import subprocess
import sys


def _is_enabled(value: str | None, default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _run(command: list[str]) -> None:
    print(f"[docker-entrypoint] running: {' '.join(command)}", flush=True)
    subprocess.run(command, check=True)


def main() -> None:
    _run([sys.executable, "scripts/migrate_database.py"])
    if _is_enabled(os.getenv("DOCKER_INIT_DEMO_DATA"), default=True):
        _run([sys.executable, "scripts/init_demo_db.py"])
        _run([sys.executable, "scripts/build_rag_index.py"])
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
