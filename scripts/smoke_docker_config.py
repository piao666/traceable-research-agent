"""Static smoke checks for one-command Docker deployment."""

from __future__ import annotations

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.requirements_manifest import read_pinned_requirements


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
    requirements = read_pinned_requirements(ROOT / "requirements.txt")
    api_requirements = read_pinned_requirements(ROOT / "requirements/api.txt")

    for service in ("api:", "streamlit:", "web:"):
        _assert(service in compose, f"compose missing {service}")
    for port in ('"8000:8000"', '"8501:8501"', '"5173:80"'):
        _assert(port in compose, f"compose missing port {port}")
    _assert("http://api:8000" in compose, "Streamlit does not target API service")
    _assert("condition: service_healthy" in compose, "Streamlit must wait for API health")
    _assert("./workspace:/app/workspace" in compose, "persistent workspace volume missing")
    _assert("path: .env" in compose, "compose must load the user .env file")
    _assert("target: api" in compose, "API must use its API-only runtime target")
    _assert("target: streamlit" in compose, "Streamlit must use its optional UI target")

    _assert("COPY requirements/api.txt" in dockerfile, "Dockerfile must install API requirements")
    _assert("pip==26.2.1" in dockerfile, "Dockerfile must bootstrap the tested pip version")
    _assert("PIP_RESUME_RETRIES=10" in dockerfile, "incomplete-download retries missing")
    _assert("--mount=type=cache" in dockerfile, "pip download cache mount missing")
    _assert("--no-cache-dir" not in dockerfile, "pip cache must remain enabled")
    _assert("python -m pip check" in dockerfile, "installed dependency validation missing")
    for unsafe in ("--trusted-host", "--no-require-hashes", "PIP_TRUSTED_HOST"):
        _assert(unsafe not in dockerfile, f"unsafe download bypass: {unsafe}")
    _assert("scripts/docker_entrypoint.py" in dockerfile, "Docker entrypoint missing")
    _assert("AS light" in dockerfile, "Docker runtime target missing")
    _assert("scripts/migrate_database.py" in entrypoint, "database migrations missing")
    _assert("scripts/init_demo_db.py" in entrypoint, "demo database initialization missing")

    for token in ("AUTH_ENABLED=false", "DEMO_API_KEY=", "QWEN_API_KEY=", "DEEPSEEK_API_KEY="):
        _assert(token in env_example, f".env.example missing {token}")
    for name in ("streamlit", "pytest", "pyarrow", "pandas", "numpy", "pydeck"):
        _assert(name not in api_requirements, f"API includes UI/test dependency: {name}")
    _assert("streamlit" in requirements and "pytest" in requirements, "full local environment changed")

    print(json.dumps({"docker_config": "ok", "services": ["api", "streamlit", "web"]}, indent=2))


if __name__ == "__main__":
    main()
