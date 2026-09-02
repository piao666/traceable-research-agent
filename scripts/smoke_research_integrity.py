"""Live localhost API smoke with an isolated database and no external credentials.

Starts/stops its own API process. Does not use or change the deployment database,
configuration, reports or uploaded files. No external service calls are needed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    opener = build_opener(ProxyHandler({}))
    temporary_root = ROOT / "workspace" / "tmp"
    temporary_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="integrity-smoke-", dir=temporary_root) as temporary:
        # Copy only code and bundled fixtures: reports, demo SQL and all trace
        # artifacts must live in this disposable repository, not the user's one.
        isolated = Path(temporary) / "project"
        for directory in ("app", "scripts", "migrations", "config"):
            shutil.copytree(ROOT / directory, isolated / directory, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copy2(ROOT / "alembic.ini", isolated / "alembic.ini")
        (isolated / "workspace" / "docs").mkdir(parents=True)
        shutil.copy2(ROOT / "workspace/docs/demo_research_note.md", isolated / "workspace/docs/demo_research_note.md")
        (isolated / "workspace/skills").mkdir()
        # Required local template only, not user-installed Skills.
        shutil.copy2(ROOT / "workspace/skills/local_audit.json", isolated / "workspace/skills/local_audit.json")
        environment = {**os.environ, "TRACE_DATABASE_PATH": str(Path(temporary) / "trace.sqlite"),
            "AUTH_ENABLED": "false", "OFFLINE_MODE": "false", "REPORT_GENERATION_MODE": "deterministic",
            "LLM_PLANNER_ENABLED": "false", "EXECUTION_MODE": "planned", "REACT_ENABLED": "false",
            "DEEP_RESEARCH_ENABLED": "false", "TAVILY_API_KEY": "", "QWEN_API_KEY": "",
            "OPENAI_API_KEY": "", "MEMORY_LLM_EXTRACTION_ENABLED": "false",
            "DEEPSEEK_API_KEY": "", "GITHUB_TOKEN": "", "MCP_REMOTE_REGISTRY_ENABLED": "false",
            "MCP_REMOTE_SERVERS": "", "MCP_CHANNEL_READONLY_SERVERS": "",
            "MCP_CHANNEL_INTERACTIVE_SERVERS": "", "MCP_CHANNEL_WRITE_SERVERS": "",
            "LLM_PROVIDER": "deterministic", "REACT_LLM_PROVIDER": "deterministic",
            "FILE_READER_ALLOWED_ROOTS": "workspace/docs", "EVIDENCE_ARTIFACT_ROOT": "workspace/artifacts",
            "REFERENCE_VERIFIER_ENABLED": "false", "SOURCE_POLICY_PATH": "config/source_policy.v2.json"}
        subprocess.run([sys.executable, "scripts/init_demo_db.py"], cwd=isolated, env=environment,
                       check=True, stdout=subprocess.DEVNULL)
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            port = reserved.getsockname()[1]
        base = f"http://127.0.0.1:{port}"

        def request(path, body=None, method=None):
            data = None if body is None else json.dumps(body).encode()
            req = Request(base + path, data=data, method=method, headers={"Content-Type": "application/json"})
            try:
                with opener.open(req, timeout=3) as response:
                    return response.status, json.load(response)
            except HTTPError as exc:
                return exc.code, json.load(exc)

        def start():
            bootstrap = "from scripts.run_offline_tests import install_network_guard; install_network_guard(); import uvicorn; uvicorn.run('app.main:app', host='127.0.0.1', port=" + str(port) + ", log_level='error')"
            process = subprocess.Popen([sys.executable, "-c", bootstrap], cwd=isolated, env=environment,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("Isolated API failed to start")
                try:
                    if request("/health")[0] == 200:
                        return process
                except (URLError, TimeoutError):
                    pass
                time.sleep(0.1)
            stop(process)
            raise RuntimeError("Isolated API startup timed out")

        def stop(process):
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

        process = start()
        try:
            status, capabilities = request("/api/runtime/capabilities")
            assert status == 200 and not capabilities["tavily_configured"]
            assert capabilities["connectivity_verified"] is False
            status, diagnostics = request("/api/runtime/diagnostics")
            assert status == 200 and all(check["status"] == "ok" for check in diagnostics["checks"]), diagnostics
            assert request("/api/tools")[0] == 200
            assert request("/api/skills")[0] == 200
            status, session = request("/api/sessions", {"title": "R5 isolated session"})
            assert status == 200, session
            session_id = session["session_id"]
            status, created = request("/api/tasks", {"task": "Compare agent frameworks using web research",
                "session_id": session_id,
                "source_mode": "real", "execution_mode_override": "planned",
                "scenario_template_key": "deep_web_research", "require_plan_approval": True})
            assert status == 200, created
            run_id = created["run_id"]
            _, readiness = request(f"/api/tasks/{run_id}/preflight")
            assert not readiness["ready"], readiness
            status, blocked = request(f"/api/tasks/{run_id}/approve-plan", {"approved": True})
            assert status == 409 and blocked["detail"]["code"] == "configuration_not_ready"
            _, state = request(f"/api/tasks/{run_id}")
            assert state["status"] == "waiting_human_plan" and state["total_tool_calls"] == 0
            _, evidence = request(f"/api/tasks/{run_id}/evidence")
            assert evidence["total_evidence_items"] == 0
            _, report = request(f"/api/reports/{run_id}")
            assert not report["exists"]
            _, detail = request(f"/api/sessions/{session_id}")
            assert len(detail["turns"]) == 1 and detail["turns"][0]["run_id"] == run_id
            _, renamed = request(f"/api/sessions/{session_id}", {"title": "R5 renamed"}, "PATCH")
            assert renamed["turn_count"] == 1
            assert request("/api/improvement/stats")[1]["total_runs"] == 0
            # Seed only the disposable fixture database; never deployment records.
            with sqlite3.connect(environment["TRACE_DATABASE_PATH"]) as fixture:
                fixture.execute("""INSERT INTO user_memories
                    (memory_id, kind, extraction_method, content, confidence, status, created_at, updated_at)
                    VALUES ('r5-fixture', 'preference', 'rule', 'Fixture memory', 0.5, 'pending', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""")
            status, memory = request("/api/memory/r5-fixture/confirm", {"approved": True})
            assert status == 200 and memory["status"] == "active", memory
            status, local = request("/api/tasks", {"task": "Read local docs and query database document metadata",
                "allowed_tools": ["file_reader", "sql_query", "report_writer"], "skill_name": "none",
                "source_mode": "real", "execution_mode_override": "planned", "require_plan_approval": True})
            assert status == 200, local
            local_id = local["run_id"]
            assert request(f"/api/tasks/{local_id}/preflight")[1]["ready"]
            status, completed = request(f"/api/tasks/{local_id}/approve-plan", {"approved": True})
            assert status == 200 and completed["status"] == "completed", completed
            _, traces = request(f"/api/tasks/{local_id}/trace")
            assert {item["tool_name"] for item in traces if item["status"] == "success"} >= {"file_reader", "sql_query"}, traces
            _, local_evidence = request(f"/api/tasks/{local_id}/evidence")
            assert local_evidence["total_evidence_items"] >= 2, local_evidence
            _, local_report = request(f"/api/reports/{local_id}")
            assert local_report["exists"] and local_report["availability"] == "available"
            assert local_report["markdown"].strip()
            report_text = local_report["markdown"]
            trace_count = len(traces)
            # The same terminal run must not execute tools twice.
            request(f"/api/tasks/{local_id}/run", {})
            assert len(request(f"/api/tasks/{local_id}/trace")[1]) == trace_count
        finally:
            stop(process)
        # Restart against the disposable DB to verify draft persistence.
        process = start()
        try:
            _, state = request(f"/api/tasks/{run_id}")
            assert state["status"] == "waiting_human_plan" and state["total_tool_calls"] == 0
            _, detail = request(f"/api/sessions/{session_id}")
            assert detail["title"] == "R5 renamed" and len(detail["turns"]) == 1
            _, memories = request("/api/memory?status=active")
            assert len(memories["memories"]) == 1
            _, audit = request("/api/memory/audit")
            assert audit[0]["action"] == "confirm" and "Fixture memory" not in str(audit)
            assert request("/api/memory/r5-fixture", method="DELETE")[0] == 200
            assert request("/api/memory")[1]["total"] == 0
            assert len(request("/api/memory/audit")[1]) == 2
            assert request(f"/api/tasks/{local_id}")[1]["status"] == "completed"
            assert request(f"/api/reports/{local_id}")[1]["markdown"] == report_text
            assert len(request(f"/api/tasks/{local_id}/trace")[1]) == trace_count
        finally:
            stop(process)
    print(json.dumps({"live_api": "passed", "missing_key_approval": "blocked",
        "draft_restart_persistence": "passed", "effective_evidence_count": 0,
        "r5_session_memory_audit_restart": "passed", "r5_modules": "passed",
        "local_file_sql_report_restart": "passed",
        "external_api_calls": 0}))


if __name__ == "__main__":
    main()
