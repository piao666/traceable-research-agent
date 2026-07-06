"""Run the MCP Source Pack Bridge as a standalone HTTP server."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
except ImportError:
    pass

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the MCP Source Pack Bridge.")
    parser.add_argument("--host", default=os.getenv("MCP_BRIDGE_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("MCP_BRIDGE_PORT", "9001")))
    parser.add_argument("--fake-mode", choices=["true", "false"], default=None)
    parser.add_argument("--providers", default=None)
    args = parser.parse_args()

    if args.fake_mode is not None:
        os.environ["MCP_BRIDGE_FAKE_MODE"] = args.fake_mode
    if args.providers:
        os.environ["MCP_BRIDGE_ENABLED_PROVIDERS"] = args.providers

    uvicorn.run(
        "app.mcp_bridge.server:app",
        host=args.host,
        port=args.port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
