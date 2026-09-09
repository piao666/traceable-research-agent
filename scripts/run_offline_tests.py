"""Offline Python test runner; external DNS/connections fail before any request.

Loopback fixture servers remain allowed. The audit guard protects this Python
process; child processes additionally inherit an offline Profile with all known
external credentials cleared. This is not an OS sandbox, so use only reviewed
local tests. Blocked attempts fail even when application code catches them.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import ipaddress
import inspect
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[1]

OFFLINE_ENVIRONMENT = {
    "RESEARCH_PROFILE": "offline",
    "OFFLINE_MODE": "true",
    "EXTERNAL_TOOLS_DEFAULT_MODE": "mock",
    "ALLOW_MOCK_FALLBACK": "false",
    "LLM_PROVIDER": "deterministic",
    "LLM_PLANNER_ENABLED": "false",
    "LLM_PLANNER_MODE": "deterministic",
    "REPORT_GENERATION_MODE": "deterministic",
    "EXECUTION_MODE": "planned",
    "REACT_ENABLED": "false",
    "MCP_BRIDGE_FAKE_MODE": "true",
    # Empty values take precedence over a developer's local .env and are
    # inherited by Python subprocess fixtures.
    "LLM_API_KEY": "",
    "QWEN_API_KEY": "",
    "DEEPSEEK_API_KEY": "",
    "TAVILY_API_KEY": "",
    "GITHUB_TOKEN": "",
    "SEMANTIC_SCHOLAR_API_KEY": "",
    "FIRECRAWL_API_KEY": "",
    "EXA_API_KEY": "",
    "CONTEXT7_API_KEY": "",
}


def is_loopback(host: object) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(str(host)).is_loopback
    except ValueError:
        return False


def install_network_guard() -> list[str]:
    violations: list[str] = []

    def guard(event: str, args: tuple) -> None:
        host = None
        if event == "socket.getaddrinfo":
            host = args[0]
        elif event in {"socket.connect", "socket.sendto"}:
            address = args[-1]
            if isinstance(address, tuple):
                host = address[0]
        if host is not None and not is_loopback(host):
            # Do not include URLs, request payloads, credentials or DNS names.
            violations.append(event)
            frame = inspect.currentframe()
            while frame:
                if Path(frame.f_code.co_filename).name.startswith("test_"):
                    print(f"OFFLINE BLOCK: {Path(frame.f_code.co_filename).name}:{frame.f_code.co_name}", file=sys.stderr)
                    break
                frame = frame.f_back
            raise OSError("External network blocked by offline test guard")

    sys.addaudithook(guard)
    return violations


@contextmanager
def isolated_test_database():
    """Isolate deployment data and remove every known external credential."""

    managed = {**OFFLINE_ENVIRONMENT, "TRACE_DATABASE_PATH": ""}
    previous = {name: os.environ.get(name) for name in managed}
    with TemporaryDirectory(prefix="traceable-offline-tests-") as temporary:
        managed["TRACE_DATABASE_PATH"] = str(Path(temporary) / "trace.sqlite")
        os.environ.update(managed)
        try:
            yield managed["TRACE_DATABASE_PATH"]
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runner", choices=["unittest", "pytest"], default="unittest")
    args = parser.parse_args()
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    violations = install_network_guard()
    with isolated_test_database():
        if args.runner == "pytest":
            import pytest
            code = int(pytest.main(["tests"]))
        else:
            result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover("tests"))
            code = 0 if result.wasSuccessful() else 1
            print("unittest discovery does not execute pytest-only function tests.")
    print(f"Offline network guard: {len(violations)} blocked external attempts")
    return 1 if violations else code


if __name__ == "__main__":
    raise SystemExit(main())
