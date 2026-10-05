"""Real-runtime smoke and API acceptance harness.

The legacy smoke path remains explicit and compatible. API acceptance is a
separate opt-in path that talks to the running service and persists redacted
response artifacts; it never changes a run's terminal state.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from app.config import settings
from app.runtime.preflight import run_runtime_preflight
from app.security.redaction import redact_sensitive_data


class AcceptanceError(RuntimeError):
    """A service response or acceptance invariant did not pass."""


def _unwrap_api(value: Any) -> Any:
    """Replay artifacts are stored as ``[http_status, payload]`` pairs."""
    if isinstance(value, list) and len(value) == 2 and isinstance(value[0], int):
        return value[1]
    return value


def _print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _json_safe(value: Any) -> Any:
    return redact_sensitive_data(value)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate the real runtime without exposing secrets.")
    parser.add_argument("--confirm-real-calls", action="store_true")
    parser.add_argument("--run-task", action="store_true", help="Run the legacy local smoke task.")
    parser.add_argument("--question", default="Summarize the FastAPI official documentation for dependency injection and cite the fetched source.")
    parser.add_argument("--r11-fetch-smoke", action="store_true")
    parser.add_argument("--static-url", default="https://example.com/")
    parser.add_argument("--browser-url", default="https://playwright.dev/python/")
    parser.add_argument("--pdf-url", default="https://arxiv.org/pdf/1706.03762")
    parser.add_argument("--acceptance-path", choices=("legacy", "api"), default="legacy")
    parser.add_argument("--base-url", default="http://127.0.0.1:18000")
    parser.add_argument("--research-mode", choices=("quick", "deep"), default="deep")
    parser.add_argument("--artifact-dir", type=Path, default=Path("tmp/acceptance"))
    parser.add_argument("--resume-run-id", help="Resume acceptance reads for an existing run; never creates or approves it.")
    parser.add_argument("--timeout-seconds", type=float, default=900.0)
    return parser


class ApiAcceptanceClient:
    """Minimal stdlib API client usable outside the application process."""

    def __init__(self, base_url: str, artifact_dir: Path, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.artifact_dir = artifact_dir
        self.timeout = timeout
        self.deadline: float | None = None
        self._counter = 0
        self.headers = {"Accept": "application/json"}
        if settings.auth_enabled and settings.demo_api_key:
            self.headers[settings.auth_header_name] = settings.demo_api_key

    def request(self, method: str, path: str, body: dict[str, Any] | None = None, *, binary: bool = False) -> tuple[int, Any, bytes]:
        data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        headers = dict(self.headers)
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = Request(self.base_url + path, data=data, headers=headers, method=method)
        remaining = self.timeout if self.deadline is None else self.deadline - time.monotonic()
        if remaining <= 0:
            self._save_response(method, path, 0, {"error": "acceptance_deadline_exceeded"})
            raise AcceptanceError(f"acceptance deadline exceeded before {method} {path}")
        try:
            with urlopen(request, timeout=remaining) as response:
                raw, status = response.read(), response.status
        except HTTPError as exc:
            raw, status = exc.read(), exc.code
        except (URLError, TimeoutError, OSError) as exc:
            self._save_response(method, path, 0, {"error_type": type(exc).__name__, "message": str(exc)[:500]})
            raise AcceptanceError(f"HTTP request failed for {method} {path}: {exc}") from exc
        if binary:
            payload: Any = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        else:
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = {"text_length": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        self._save_response(method, path, status, payload)
        return status, payload, raw

    def _save_response(self, method: str, path: str, status: int, payload: Any) -> None:
        self._counter += 1
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = path.split("?", 1)[0].strip("/").replace("/", "_") or "root"
        name = f"{self._counter:02d}-{method.lower()}-{artifact_path}.json"
        record = {"http_status": status, "payload": _json_safe(payload)}
        (self.artifact_dir / name).write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    def json(self, method: str, path: str, body: Any = None) -> Any:
        """Return any JSON value; collection endpoints (notably Trace) are lists."""
        status, payload, _ = self.request(method, path, body)
        if status < 200 or status >= 300:
            raise AcceptanceError(f"HTTP {status} for {method} {path}")
        return payload


def _get(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def _trace_items(traces: Any) -> list[dict[str, Any]]:
    traces = _unwrap_api(traces)
    if isinstance(traces, dict):
        traces = traces.get("traces", [])
    return [item for item in traces if isinstance(item, dict)] if isinstance(traces, list) else []


def _provider_is_real(provider: Any) -> bool:
    value = str(provider or "").strip().lower()
    return bool(value) and value not in {"mock", "fallback", "deterministic", "offline", "fake", "fixture"}


def _valid_page(page: Any) -> bool:
    """Validate the persisted fetch contract, rather than trusting a flag.

    A page is substantive only when both returned and source text, their
    hashes, quality metadata, and a real provider are present and consistent.
    """
    if not isinstance(page, dict):
        return False
    content = page.get("content")
    if not isinstance(content, str) or len(content.strip()) < 200:
        return False
    content_hash = page.get("content_hash")
    source_hash = page.get("source_content_hash")
    if not isinstance(content_hash, str) or not isinstance(source_hash, str):
        return False
    # The public Trace can return a capped view of a much longer fetched body.
    # In that case both hashes identify the *source* body, not the view. Do not
    # compare a 6000-character view to the full-body hash; require the explicit
    # truncation marker and cross-check that source hash with persisted evidence.
    view_truncated = page.get("view_truncated") is True
    if content_hash != source_hash:
        return False
    source_content = page.get("source_content")
    if source_content is not None:
        if not isinstance(source_content, str) or source_hash != hashlib.sha256(source_content.encode("utf-8")).hexdigest():
            return False
        if view_truncated and not source_content.startswith(content):
            return False
    elif not isinstance(page.get("source_content_length"), int) or page["source_content_length"] < len(content):
        return False
    if view_truncated:
        if page.get("source_content_length", len(source_content) if isinstance(source_content, str) else 0) <= len(content):
            return False
    elif content_hash != hashlib.sha256(content.encode("utf-8")).hexdigest():
        return False
    quality = page.get("quality")
    if not isinstance(quality, dict) or quality.get("usable") is not True:
        return False
    if str(page.get("fetch_status") or "").lower() not in {"success", "fetched", "usable", "partial"}:
        return False
    if not _provider_is_real(page.get("provider")):
        return False
    if page.get("cache_hit") is True or str(page.get("fetch_backend") or "").lower() == "cache":
        return False
    role = str(page.get("evidence_role") or page.get("role") or "").lower()
    basis = str(page.get("content_basis") or "").lower()
    # Older persisted fetch pages predate the optional role projection.  The
    # page contract proves real bytes/provider/quality; final citation support
    # is checked separately from the adopted report revision.
    role_ok = not role or role in {"support", "primary", "primary_content", "official"}
    return role_ok and basis in {"full_text", "partial", "article"}


def _substantive_evidence(evidence: Any, traces: Any) -> bool:
    """Require full-text fetch evidence, never discovery snippets alone."""
    evidence = _unwrap_api(evidence)
    traces = _unwrap_api(traces)
    items = evidence.get("evidence_items", evidence.get("evidence", [])) if isinstance(evidence, dict) else evidence
    if isinstance(items, dict):
        items = items.get("items", [])
    if not isinstance(items, list):
        items = []
    persisted_hashes = {
        str((item.get("metadata") or {}).get("content_hash") or "")
        for item in items
        if isinstance(item, dict)
        and item.get("tool_name") == "web_fetcher"
        and item.get("status") == "success"
        and not item.get("is_mock")
        and not item.get("is_fallback")
        and str((item.get("metadata") or {}).get("content_basis") or "").lower() in {"full_text", "partial", "article"}
        and _provider_is_real((item.get("metadata") or {}).get("provider"))
    }
    trace_items = _trace_items(traces)
    for item in trace_items:
        if not isinstance(item, dict) or item.get("tool_name") != "web_fetcher" or item.get("status") != "success":
            continue
        output = item.get("output") if isinstance(item.get("output"), dict) else {}
        # Real fetches persist structured pages.  A free-form ``full_text``
        # string is not evidence: it can be a discovery/model summary.
        pages = output.get("pages")
        if not isinstance(pages, list):
            continue
        for page in pages:
            if _valid_page(page) and page.get("content_hash") in persisted_hashes:
                return True
    return False


def _r11_fetch_smoke(args: argparse.Namespace) -> int:
    """Legacy explicit fetch smoke; separate from API acceptance semantics."""
    from app.retrieval.remote_extract import configured_remote_providers
    from app.tools.web_fetcher import web_fetch
    cases = [("static_http", args.static_url, ["http"]), ("browser", args.browser_url, ["browser"]), ("pdf", args.pdf_url, [])]
    remote = configured_remote_providers(provider_order=settings.fetch_remote_extract_provider_order)
    if settings.fetch_remote_extract_enabled and any(provider.available() for provider in remote):
        cases.append(("remote_extract", args.static_url, ["remote_extract"]))
    results = []
    for name, url, preferred in cases:
        result = web_fetch({"urls": [url], "preferred_backends": preferred, "max_chars": 8000, "timeout_seconds": settings.fetch_browser_timeout_seconds, "batch_timeout_seconds": min(120, settings.fetch_browser_timeout_seconds + 10)}, settings_obj=settings)
        page = result.output["pages"][0] if result.output and result.output.get("pages") else {}
        results.append({"case": name, "success": result.success, "fetch_status": page.get("fetch_status"), "fetch_backend": page.get("fetch_backend"), "provider": page.get("provider"), "content_basis": page.get("content_basis"), "failure_code": page.get("error_code") or page.get("error"), "retrieval_attempts": page.get("retrieval_attempts") or []})
    ready = bool(results) and all(item["success"] for item in results)
    _print({"stage": "r11_fetch_smoke", "ready": ready, "cases": results, "remote_case": "executed" if any(item["case"] == "remote_extract" for item in results) else "not_configured"})
    return 0 if ready else 1


def evaluate_acceptance(*, research_mode: str, status: dict[str, Any], plan: dict[str, Any], traces: Any, evidence: Any, report: dict[str, Any], download_sha256: str | None, child_observations: list[tuple[Any, Any]] | None = None) -> dict[str, Any]:
    """Pure strict acceptance decision, suitable for replay contract tests."""
    status = _unwrap_api(status) if isinstance(_unwrap_api(status), dict) else {}
    plan = _unwrap_api(plan) if isinstance(_unwrap_api(plan), dict) else {}
    report = _unwrap_api(report) if isinstance(_unwrap_api(report), dict) else {}
    traces = _unwrap_api(traces)
    evidence = _unwrap_api(evidence)
    reasons: list[str] = []
    terminal_decision = status.get("terminal_decision") if isinstance(status.get("terminal_decision"), dict) else {}
    terminal_status = status.get("status") or terminal_decision.get("status")
    if terminal_status != "completed":
        reasons.append(f"terminal status is {terminal_status!r}")
    if status.get("citation_evaluated") is not True:
        reasons.append("final citation validation was not evaluated")
    if _get(report, "availability") != "available" or not _get(report, "exists"):
        reasons.append("report is not available")
    if status.get("source_mode") != "real":
        reasons.append("source_mode is not real")
    mode = _get(status, "research_mode") or _get(plan, "research_mode")
    if research_mode == "deep" and mode != "deep":
        reasons.append(f"deep request was recorded as {mode!r}")
    execution = _get(status, "execution_mode") or _get(plan, "execution_mode")
    if research_mode == "deep" and execution != "deep_research_v2":
        reasons.append(f"deep request used {execution!r}, expected deep_research_v2")
    if research_mode == "quick" and execution not in (None, "planned"):
        reasons.append(f"quick request used {execution!r}")
    integrity = report.get("integrity") if isinstance(report.get("integrity"), dict) else {}
    # The public plan projection omits internal outcome/integrity fields. The
    # status and report endpoints carry the authoritative persisted decision.
    outcome = status.get("research_outcome") if isinstance(status.get("research_outcome"), dict) else plan.get("research_outcome") if isinstance(plan.get("research_outcome"), dict) else {}
    evidence_assessment = outcome.get("evidence_assessment") if isinstance(outcome.get("evidence_assessment"), dict) else {}
    if evidence_assessment.get("passed") is not True:
        reasons.append("required evidence assessment did not pass")
    report_integrity = plan.get("report_integrity") if isinstance(plan.get("report_integrity"), dict) else {}
    if report_integrity:
        if report_integrity.get("status") != "passed":
            reasons.append("persisted report integrity did not pass")
        elif integrity and integrity.get("status") != report_integrity.get("status"):
            reasons.append("report endpoint integrity differs from persisted plan")
    elif terminal_decision.get("status") != "completed":
        reasons.append("persisted terminal integrity decision did not pass")
    report_decision = report.get("terminal_decision") if isinstance(report.get("terminal_decision"), dict) else {}
    if not report_decision or any(
        report_decision.get(field) != terminal_decision.get(field)
        for field in ("decision_input_hash", "validation_identity", "report_sha256")
    ):
        reasons.append("report endpoint decision differs from persisted terminal decision")
    if isinstance(status.get("citation_unsupported"), int) and status["citation_unsupported"]:
        reasons.append("final report contains unsupported citation occurrences")
    report_hash = _get(status, "report_hash", "report_sha256") or _get(terminal_decision, "report_hash", "report_sha256") or _get(report, "report_hash", "report_sha256") or _get(integrity, "report_hash", "report_sha256")
    if not report_hash:
        reasons.append("terminal/report hash is unavailable")
    elif not download_sha256 or download_sha256 != report_hash:
        reasons.append("download sha256 differs from terminal/report hash")
    revision_id = terminal_decision.get("report_revision_id")
    plan_revision = plan.get("report_revision_id") or (plan.get("report_generation") or {}).get("report_revision_id")
    if not revision_id:
        reasons.append("terminal report revision is unavailable")
    elif not plan_revision:
        reasons.append("plan report revision is unavailable")
    elif plan_revision != revision_id:
        reasons.append("terminal report revision differs from plan revision")
    manifest = (plan.get("report_generation") or {}).get("manifest_sha256") or plan.get("report_manifest_sha256")
    terminal_manifest = terminal_decision.get("manifest_sha256") or terminal_decision.get("report_manifest_sha256")
    if not manifest or not terminal_manifest or manifest != terminal_manifest:
        reasons.append("report revision manifest is unavailable or mismatched")
    generation = plan.get("report_generation") if isinstance(plan.get("report_generation"), dict) else {}
    validation_identity = generation.get("validation_identity")
    terminal_validation_identity = terminal_decision.get("validation_identity")
    if not generation.get("adopted") and research_mode != "quick":
        reasons.append("final report revision was not adopted")
    if not validation_identity or not terminal_validation_identity or validation_identity != terminal_validation_identity:
        reasons.append("report validation identity is unavailable or mismatched")
    for field in ("evidence_snapshot_id", "writing_manifest_hash", "validator_version", "manifest_sha256"):
        if not generation.get(field) or generation.get(field) != terminal_decision.get(field):
            reasons.append(f"report {field} is unavailable or mismatched")
    # Deep-research child runs persist their own Trace and Evidence. Match a
    # fetch page to evidence within the same run; never mix hashes across runs.
    observations = [(evidence, traces), *(child_observations or [])]
    if not any(_substantive_evidence(child_evidence, child_traces) for child_evidence, child_traces in observations):
        reasons.append("no substantive full-text fetch evidence")
    llm_traces = _trace_items(traces)
    if not any(item.get("tool_name") in {"report_synthesis", "llm_report", "planner"} and isinstance(item.get("metadata"), dict) and _provider_is_real(item["metadata"].get("provider")) and (item.get("token_in", 0) or item.get("token_out", 0)) for item in llm_traces):
        reasons.append("no real LLM trace with provider and usage")
    return {"passed": not reasons, "reasons": reasons, "terminal": terminal_status, "report_sha256": report_hash, "download_sha256": download_sha256}


def _load_dispatched_child_observations(
    client: ApiAcceptanceClient, parent_run_id: str, parent_traces: Any, artifact_dir: Path,
) -> list[tuple[Any, Any]]:
    """Read only children dispatched by this run and verify their parent link."""
    dispatched = [
        item["output"]
        for item in _trace_items(parent_traces)
        if item.get("tool_name") == "research_node_dispatch"
        and item.get("status") == "success"
        and item.get("run_id") == parent_run_id
        and isinstance(item.get("output"), dict)
        and isinstance(item["output"].get("child_run_id"), str)
    ]
    parents: dict[str, str] = {}
    for output in dispatched:
        child_id = output["child_run_id"]
        actual_parent = output.get("parent_run_id") or parent_run_id
        if not isinstance(actual_parent, str) or (
            child_id in parents and parents[child_id] != actual_parent
        ):
            raise AcceptanceError("conflicting dispatched child parent links")
        parents[child_id] = actual_parent
    statuses = {
        child_id: client.json("GET", f"/api/tasks/{child_id}")
        for child_id in sorted(parents)
    }
    for child_id, actual_parent in parents.items():
        status = statuses[child_id]
        if status.get("run_id") != child_id or status.get("parent_run_id") != actual_parent:
            raise AcceptanceError(f"dispatched child {child_id} has no matching parent link")
        if status.get("root_run_id") not in (None, parent_run_id):
            raise AcceptanceError(f"dispatched child {child_id} has a different root")
        seen = {child_id}
        ancestor = actual_parent
        while ancestor != parent_run_id:
            if ancestor in seen or ancestor not in parents:
                raise AcceptanceError(f"dispatched child {child_id} has an invalid ancestor chain")
            seen.add(ancestor)
            ancestor = parents[ancestor]
        parent_status = statuses.get(actual_parent)
        if parent_status and status.get("research_scope_id") != parent_status.get("research_scope_id"):
            raise AcceptanceError(f"dispatched child {child_id} has a different scope")
    observations: list[tuple[Any, Any]] = []
    for child_id in sorted(parents):
        child_status = statuses[child_id]
        child_traces = client.json("GET", f"/api/tasks/{child_id}/trace")
        child_evidence = client.json("GET", f"/api/tasks/{child_id}/evidence")
        for name, payload in (("status", child_status), ("trace", child_traces), ("evidence", child_evidence)):
            (artifact_dir / f"{child_id}-{name}.json").write_text(
                json.dumps(_json_safe(payload), ensure_ascii=False, indent=2, default=str), encoding="utf-8"
            )
        observations.append((child_evidence, child_traces))
    return observations


def run_api_acceptance(args: argparse.Namespace) -> int:
    started = time.monotonic()
    client = ApiAcceptanceClient(args.base_url, args.artifact_dir, args.timeout_seconds)
    client.deadline = started + args.timeout_seconds
    args.artifact_dir.mkdir(parents=True, exist_ok=True)
    # Record only non-secret fingerprints so a replay can prove which runtime
    # configuration/code produced it without copying .env into artifacts.
    safe_config = settings.get_safe_runtime_config_summary()
    script_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    (args.artifact_dir / "acceptance-manifest.json").write_text(json.dumps({
        "stage": "api_acceptance", "started_at_epoch": time.time(),
        "base_url": args.base_url, "research_mode": args.research_mode,
        "real_calls": True, "runtime_config_sha256": hashlib.sha256(
            json.dumps(safe_config, sort_keys=True, default=str).encode()).hexdigest(),
        "script_sha256": script_hash,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    run_id = args.resume_run_id
    plan_approvals = 0
    if run_id:
        status = client.json("GET", f"/api/tasks/{run_id}")
    else:
        task = client.json("POST", "/api/tasks", {"task": args.question, "report_type": "summary", "source_mode": "real", "research_mode": args.research_mode, "require_plan_approval": True})
        run_id = task.get("run_id")
        if not run_id:
            raise AcceptanceError("POST /api/tasks did not return run_id")
        status = client.json("GET", f"/api/tasks/{run_id}")
        if status.get("status") == "waiting_human_plan":
            client.json("POST", f"/api/tasks/{run_id}/approve-plan?start_async=true", {"approved": True, "comment": "API acceptance plan approval"})
            plan_approvals += 1
        elif status.get("status") == "waiting_human":
            _print({"stage": "api_acceptance", "ready": False, "needs_attention": True, "run_id": run_id, "message": "Run requires high-risk human confirmation; no automatic approval was issued."})
            return 2
        else:
            client.json("POST", f"/api/tasks/{run_id}/run_async")
    # The acceptance budget covers create, approval, execution, reads, and
    # download.  It must not restart after POST /api/tasks.
    deadline = started + args.timeout_seconds
    while True:
        status = client.json("GET", f"/api/tasks/{run_id}")
        state = status.get("status")
        if state in {"completed", "failed", "incomplete", "cancelled"}:
            break
        if state == "waiting_human_plan":
            if plan_approvals:
                plan = client.json("GET", f"/api/tasks/{run_id}/plan")
                traces = client.json("GET", f"/api/tasks/{run_id}/trace")
                for name, payload in (("status", status), ("plan", plan), ("trace", traces)):
                    (args.artifact_dir / f"{run_id}-{name}.json").write_text(
                        json.dumps(_json_safe(payload), ensure_ascii=False, indent=2, default=str), encoding="utf-8"
                    )
                _print({"stage": "api_acceptance", "ready": False, "needs_attention": True,
                        "run_id": run_id, "message": "Plan returned to review after approval; no automatic reapproval was issued."})
                return 2
            client.json("POST", f"/api/tasks/{run_id}/approve-plan?start_async=true", {"approved": True, "comment": "API acceptance plan approval"})
            plan_approvals += 1
        elif state == "waiting_human":
            _print({"stage": "api_acceptance", "ready": False, "needs_attention": True, "run_id": run_id, "message": "Run requires high-risk human confirmation; no automatic approval was issued."})
            return 2
        if time.monotonic() >= deadline:
            raise AcceptanceError(f"timeout waiting for terminal state; run_id={run_id}")
        time.sleep(min(2.0, max(0.1, deadline - time.monotonic())))
    plan = client.json("GET", f"/api/tasks/{run_id}/plan")
    traces = client.json("GET", f"/api/tasks/{run_id}/trace")
    evidence = client.json("GET", f"/api/tasks/{run_id}/evidence")
    report = client.json("GET", f"/api/reports/{run_id}")
    child_observations = _load_dispatched_child_observations(client, run_id, traces, args.artifact_dir) if args.research_mode == "deep" else []
    # Stable replay names are consumed by the A0 baseline auditor.  Keep the
    # numbered request log too, but make each canonical response independently
    # inspectable and preserve the [status, payload] shape used by old runs.
    for name, payload in (("status", status), ("plan", plan), ("trace", traces), ("evidence", evidence), ("report", report)):
        (args.artifact_dir / f"{run_id}-{name}.json").write_text(
            json.dumps(_json_safe(payload), ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
    download_status, download, raw_download = client.request("GET", f"/api/reports/{run_id}/download", binary=True)
    if download_status != 200:
        raise AcceptanceError(f"HTTP {download_status} for report download; report is not downloadable")
    download_path = args.artifact_dir / f"{run_id}-report.md"
    download_path.write_bytes(raw_download)
    result = evaluate_acceptance(research_mode=args.research_mode, status=status, plan=plan, traces=traces, evidence=evidence, report=report, download_sha256=download.get("sha256"), child_observations=child_observations)
    result.update({"stage": "api_acceptance", "run_id": run_id, "artifact_dir": str(args.artifact_dir), "download_path": str(download_path), "real_calls": True})
    _print(result)
    return 0 if result["passed"] else 1


def _legacy_main(args: argparse.Namespace) -> int:
    preflight = run_runtime_preflight(settings)
    _print({"stage": "preflight", **preflight})
    if not preflight["ready"]:
        return 1
    if not args.run_task:
        return 0
    if settings.report_generation_mode != "llm":
        _print({"stage": "task", "ready": False, "error_type": "invalid_configuration", "message": "Legacy smoke requires REPORT_GENERATION_MODE=llm."})
        return 2
    from app.agent.dispatcher import run_task_by_mode
    from app.agent.planner import plan_task
    from app.database import SessionLocal, init_db
    from app.skills.registry import init_skill_registry
    from app.tools.defaults import register_default_tools
    from app.trace import store
    try:
        init_db(); register_default_tools(); init_skill_registry(ROOT / "workspace" / "skills")
        with SessionLocal() as db:
            # Match the frontend/API default: omit an explicit allow-list so
            # the planner derives the enabled read-only registry tools.
            run = store.create_agent_run(db, task=args.question, report_type="summary", source_mode="real", allowed_tools=None, run_config_snapshot=json.dumps(settings.get_safe_runtime_config_summary(), ensure_ascii=False))
            plan = plan_task(run.task, None, "real", planner_mode="deterministic", scenario_template="deep_web_research", execution_mode_override="planned", skill_name="deep_web_research")
            store.update_agent_run_plan(db, run.run_id, plan)
            result = run_task_by_mode(db, run.run_id, settings.model_copy(update={"deep_research_enabled": False}))
            final = store.get_fresh_agent_run(db, run.run_id)
            summary = {"stage": "legacy_smoke", "run_id": run.run_id, "status": final.status if final else result.get("status", "unknown"), "report_generated": bool(final and final.report_path), "source_mode": "real"}
            _print(summary)
            return 0 if summary["status"] == "completed" and summary["report_generated"] else 1
    except Exception:
        _print({"stage": "legacy_smoke", "status": "failed", "error_type": "internal_error", "message": "Legacy smoke failed; inspect persisted run and logs."})
        return 1


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.confirm_real_calls:
        _print({"ready": False, "error_type": "confirmation_required", "message": "No real call was made. Re-run with --confirm-real-calls."})
        return 2
    if args.acceptance_path == "api":
        if settings.research_profile == "offline" or settings.offline_mode:
            _print({"ready": False, "error_type": "offline_profile", "message": "API acceptance requires the deep or standard profile."})
            return 2
        try:
            return run_api_acceptance(args)
        except AcceptanceError as exc:
            _print({"stage": "api_acceptance", "ready": False, "error_type": "acceptance_error", "message": str(exc)})
            return 1
    if args.r11_fetch_smoke:
        return _r11_fetch_smoke(args)
    return _legacy_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
