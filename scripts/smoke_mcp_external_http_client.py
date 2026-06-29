"""Smoke test a real external HTTP client against the MCP endpoint.

Unlike the TestClient demo, this starts uvicorn in a child process and talks to
the app over localhost TCP with requests.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_TOOLS = {
    "file_reader",
    "rag_search",
    "sql_query_readonly",
    "github_search",
    "tavily_search",
    "trace_reader",
    "report_reader",
}


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def request_json(
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout: float = 10,
) -> dict[str, Any]:
    response = requests.request(method, url, json=payload, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    assert_true(isinstance(data, dict), f"{method} {url} did not return a JSON object")
    return data


def rpc(base_url: str, request_id: int, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    return request_json(
        "POST",
        f"{base_url}/mcp",
        payload={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        },
    )


def rpc_content(payload: dict[str, Any]) -> dict[str, Any]:
    assert_true(payload.get("error") is None, f"Unexpected MCP error: {payload.get('error')}")
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    content = result.get("content") if isinstance(result.get("content"), list) else []
    assert_true(bool(content), "MCP result content is empty")
    first = content[0] if isinstance(content[0], dict) else {}
    data = first.get("json")
    assert_true(isinstance(data, dict), "MCP content item did not include JSON data")
    return data


def wait_for_server(base_url: str, process: subprocess.Popen[str]) -> None:
    deadline = time.time() + 30
    last_error = ""
    while time.time() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=2)
            raise RuntimeError(
                f"uvicorn exited early with code {process.returncode}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}"
            )
        try:
            health = requests.get(f"{base_url}/health", timeout=1)
            if health.status_code == 200:
                return
            last_error = f"HTTP {health.status_code}: {health.text[:200]}"
        except requests.RequestException as exc:
            last_error = str(exc)
        time.sleep(0.25)
    raise TimeoutError(f"uvicorn did not become ready: {last_error}")


def create_demo_run(base_url: str) -> str:
    created = request_json(
        "POST",
        f"{base_url}/api/tasks",
        payload={
            "task": "Read local docs and generate a report for external MCP HTTP smoke.",
            "report_type": "markdown",
            "source_mode": "mock",
            "allowed_tools": ["file_reader", "report_writer"],
            "execution_mode_override": "planned",
        },
    )
    run_id = str(created["run_id"])
    run = request_json("POST", f"{base_url}/api/tasks/{run_id}/run", payload={})
    assert_true(run.get("status") == "completed", f"Demo run did not complete: {run}")
    return run_id


def main() -> None:
    port = free_port()
    base_url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env.update(
        {
            "PYTHONIOENCODING": "utf-8",
            "AUTH_ENABLED": "false",
            "LLM_PROVIDER": "deterministic",
            "LLM_PLANNER_ENABLED": "false",
            "GITHUB_TOOL_DEFAULT_MODE": "mock",
            "TAVILY_FALLBACK_TO_MOCK": "true",
            "MCP_REMOTE_REGISTRY_ENABLED": "false",
        }
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        wait_for_server(base_url, process)
        run_id = create_demo_run(base_url)

        initialized = rpc(base_url, 1, "initialize")
        assert_true(initialized.get("error") is None, f"initialize failed: {initialized}")

        listed = rpc(base_url, 2, "tools/list")
        assert_true(listed.get("error") is None, f"tools/list failed: {listed}")
        result = listed.get("result") if isinstance(listed.get("result"), dict) else {}
        tools = result.get("tools") if isinstance(result.get("tools"), list) else []
        names = {tool.get("name") for tool in tools if isinstance(tool, dict)}
        assert_true(EXPECTED_TOOLS <= names, f"Missing MCP tools: {sorted(EXPECTED_TOOLS - names)}")
        assert_true("report_writer" not in names, "report_writer was exposed through MCP")

        file_call = rpc(
            base_url,
            3,
            "tools/call",
            {
                "name": "file_reader",
                "arguments": {"path": "demo_research_note.md", "max_chars": 300},
                "_trace": {"run_id": run_id, "step_no": 77},
            },
        )
        file_payload = rpc_content(file_call)
        assert_true(file_payload.get("success") is True, f"file_reader failed: {file_payload}")

        trace_call = rpc(
            base_url,
            4,
            "tools/call",
            {"name": "trace_reader", "arguments": {"run_id": run_id}},
        )
        trace_payload = rpc_content(trace_call)
        trace_output = trace_payload.get("output") if isinstance(trace_payload.get("output"), dict) else {}
        traces = trace_output.get("traces") if isinstance(trace_output.get("traces"), list) else []
        assert_true(
            any(trace.get("tool_name") == "file_reader" and trace.get("step_no") == 77 for trace in traces if isinstance(trace, dict)),
            "external HTTP MCP call did not write trace row",
        )

        report_call = rpc(
            base_url,
            5,
            "tools/call",
            {"name": "report_reader", "arguments": {"run_id": run_id}},
        )
        report_payload = rpc_content(report_call)
        report_output = report_payload.get("output") if isinstance(report_payload.get("output"), dict) else {}
        assert_true(report_output.get("exists") is True, "report_reader did not find generated report")

        summary = {
            "mcp_external_http_client": "ok",
            "base_url": base_url,
            "run_id": run_id,
            "discovered_tools": sorted(names),
            "mcp_trace_written": True,
            "report_exists": True,
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    main()
