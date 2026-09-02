"""Offline Python test runner; external DNS/connections fail before any request.

Loopback fixture servers remain allowed. This in-process guard is not an OS
sandbox and does not cover subprocess networking. Use only reviewed local tests.
Blocked attempts fail the gate even when application code catches the exception.
"""
from __future__ import annotations

import argparse
import ipaddress
import inspect
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runner", choices=["unittest", "pytest"], default="unittest")
    args = parser.parse_args()
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    violations = install_network_guard()
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
