"""Offline Python test runner; external DNS/connections fail before any request.

Loopback fixture servers remain allowed. The audit guard protects this Python
process; child processes additionally inherit an offline Profile with all known
external credentials cleared. This is not an OS sandbox, so use only reviewed
local tests. Blocked attempts fail even when application code catches them.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import gc
import ipaddress
import inspect
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from typing import Iterator

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


def install_network_guard(current_test: list[str | None] | None = None) -> list[str]:
    violations: list[str] = []

    def origin() -> str:
        if current_test and current_test[0]:
            return current_test[0]
        frame = inspect.currentframe()
        while frame:
            filename = Path(frame.f_code.co_filename).name
            if filename.startswith("test_"):
                return f"{filename}:{frame.f_code.co_name}"
            frame = frame.f_back
        # Collection/import-time attempts have no test frame. Keep this
        # diagnostic useful without exposing paths, hosts, or request data.
        return "<no-test-frame>"

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
            violation_origin = origin()
            violations.append(f"{event}:{violation_origin}")
            # pytest captures ``sys.stderr`` for tests that intentionally
            # handle I/O errors; the original stream keeps the fail-closed
            # audit origin visible to the wrapper operator.
            print(f"OFFLINE BLOCK: {violation_origin}", file=sys.__stderr__)
            raise OSError("External network blocked by offline test guard")

    sys.addaudithook(guard)
    return violations


class OfflinePytestOriginPlugin:
    """Expose the active pytest item to the audit hook without test data."""

    def __init__(self, current_test: list[str | None]) -> None:
        self.current_test = current_test

    def pytest_runtest_protocol(self, item: object, nextitem: object) -> Iterator[None]:
        location = getattr(item, "location", ("<unknown>", 0, "<unknown>"))
        self.current_test[0] = f"{Path(str(location[0])).name}:{location[2]}"
        yield
        self.current_test[0] = None

    pytest_runtest_protocol.pytest_impl = {"hookwrapper": True}  # type: ignore[attr-defined]


def dispose_isolated_application_database() -> None:
    """Release the process-global SQLAlchemy pool before Windows temp cleanup."""

    database_module = sys.modules.get("app.database")
    engine = getattr(database_module, "engine", None)
    if engine is not None:
        engine.dispose()
    gc.collect()


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
            # ``app.database`` is imported during pytest collection after the
            # isolated path is configured. Its module-global pool can retain a
            # SQLite handle until interpreter shutdown, which prevents
            # TemporaryDirectory cleanup on Windows.
            dispose_isolated_application_database()
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
    current_test: list[str | None] = [None]
    violations = install_network_guard(current_test)
    with isolated_test_database():
        if args.runner == "pytest":
            import pytest
            code = int(pytest.main(["tests"], plugins=[OfflinePytestOriginPlugin(current_test)]))
        else:
            result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover("tests"))
            code = 0 if result.wasSuccessful() else 1
            print("unittest discovery does not execute pytest-only function tests.")
    print(f"Offline network guard: {len(violations)} blocked external attempts")
    if violations:
        print(f"Offline network guard origins: {', '.join(violations)}")
    return 1 if violations else code


if __name__ == "__main__":
    raise SystemExit(main())
