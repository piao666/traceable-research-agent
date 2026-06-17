"""Safe smoke for LLM config and provider factory."""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import settings
from app.llm.providers import create_llm_client


def main() -> None:
    clients = {
        provider: create_llm_client(settings, provider).describe()
        for provider in ["deterministic", "qwen", "deepseek"]
    }
    payload = {
        "config": settings.get_safe_llm_config_summary(),
        "clients": clients,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
