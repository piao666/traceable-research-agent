"""Static smoke checks for Docker one-command demo deployment config."""

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
    env_example = _read(".env.docker.example")
    gitignore = _read(".gitignore")
    dockerignore = _read(".dockerignore")
    entrypoint = _read("scripts/docker_entrypoint.py")
    real_rag = _read("docker-compose.real-rag.yml")

    _assert("api:" in compose, "docker-compose.yml missing api service")
    _assert("streamlit:" in compose, "docker-compose.yml missing streamlit service")
    _assert('"8000:8000"' in compose or "'8000:8000'" in compose, "api port 8000 not exposed")
    _assert('"8501:8501"' in compose or "'8501:8501'" in compose, "streamlit port 8501 not exposed")
    _assert("STREAMLIT_API_BASE_URL" in compose, "streamlit API base env missing")
    _assert("http://api:8000" in compose, "streamlit does not target api service")
    _assert("condition: service_healthy" in compose, "streamlit does not wait for api health")
    _assert("healthcheck:" in compose and "/health" in compose, "api healthcheck missing")
    _assert("./workspace:/app/workspace" in compose, "workspace volume missing")
    _assert("env_file:" in compose and ".env.docker" in compose, "docker env file not configured")
    for secret_name in ("GITHUB_TOKEN", "TAVILY_API_KEY", "QWEN_API_KEY", "DEEPSEEK_API_KEY"):
        _assert(
            f"${{{secret_name}" not in compose,
            f"docker-compose.yml should not inherit host {secret_name}; use .env.docker",
        )

    _assert("EXPOSE 8000 8501" in dockerfile, "Dockerfile should expose api and streamlit ports")
    _assert("scripts/docker_entrypoint.py" in dockerfile, "Dockerfile does not use docker entrypoint")
    _assert("requirements-docker-light.txt" in dockerfile, "Dockerfile should use light requirements")

    for token in (
        "AUTH_ENABLED=false",
        "EXECUTION_MODE=planned",
        "LLM_PLANNER_ENABLED=false",
        "GITHUB_TOKEN=",
        "TAVILY_API_KEY=",
        "QWEN_API_KEY=",
        "DEEPSEEK_API_KEY=",
        "RAG_REAL_BACKEND_ENABLED=false",
        "RAG_EMBEDDING_BACKEND=deterministic",
        "RAG_VECTOR_BACKEND=json",
    ):
        _assert(token in env_example, f".env.docker.example missing {token}")

    _assert(".env.*" in gitignore and "!.env.docker.example" in gitignore, ".gitignore env exception missing")
    _assert(".env.*" in dockerignore and "!.env.docker.example" in dockerignore, ".dockerignore env handling missing")
    _assert("init_demo_db.py" in entrypoint and "build_rag_index.py" in entrypoint, "entrypoint init steps missing")
    _assert("sentence_transformers" in real_rag and "/models/bge-small-zh-v1.5" in real_rag, "real-rag override incomplete")

    print(
        json.dumps(
            {
                "docker_config": "ok",
                "services": ["api", "streamlit"],
                "ports": [8000, 8501],
                "healthcheck": "ok",
                "env_example": "ok",
                "real_rag_override": "ok",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
