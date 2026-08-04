"""Static smoke checks for one-command Docker deployment."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    compose = _read("docker-compose.yml")
    dockerfile = _read("Dockerfile")
    env_example = _read(".env.example")
    entrypoint = _read("scripts/docker_entrypoint.py")
    requirements = _read("requirements.txt")

    for service in ("api:", "streamlit:"):
        _assert(service in compose, f"compose missing {service}")
    for port in ('"8000:8000"', '"8501:8501"'):
        _assert(port in compose, f"compose missing port {port}")
    _assert("http://api:8000" in compose, "Streamlit does not target API service")
    _assert("condition: service_healthy" in compose, "Streamlit must wait for API health")
    _assert("./workspace:/app/workspace" in compose, "persistent workspace volume missing")
    _assert("path: .env" in compose, "compose must load the user .env file")
    _assert("target: light" in compose, "compose must use the self-hosted runtime target")

    _assert("COPY requirements.txt" in dockerfile, "Dockerfile must install pinned requirements")
    _assert("scripts/docker_entrypoint.py" in dockerfile, "Docker entrypoint missing")
    _assert("AS light" in dockerfile, "Docker runtime target missing")
    _assert("scripts/migrate_database.py" in entrypoint, "database migrations missing")
    _assert("scripts/init_demo_db.py" in entrypoint, "demo database initialization missing")

    for token in ("AUTH_ENABLED=false", "DEMO_API_KEY=", "QWEN_API_KEY=", "DEEPSEEK_API_KEY="):
        _assert(token in env_example, f".env.example missing {token}")
    for line in requirements.splitlines():
        if line.strip():
            _assert("==" in line, f"dependency is not pinned: {line}")

    print(json.dumps({"docker_config": "ok", "services": ["api", "streamlit"]}, indent=2))


if __name__ == "__main__":
    main()
