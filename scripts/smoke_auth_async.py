"""Smoke check for single-instance API-key and async runtime defaults."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import Settings


def main() -> None:
    settings = Settings()
    summary = settings.get_safe_auth_config_summary()
    assert settings.auth_enabled is False
    assert settings.async_run_enabled is True
    assert summary["demo_api_key_configured"] is False
    assert "tenant_header_name" not in summary
    assert "default_user_id" not in summary
    print(json.dumps({"auth": "ok", "async": "ok", "single_instance": "ok"}))


if __name__ == "__main__":
    main()
