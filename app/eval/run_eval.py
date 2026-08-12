"""Run deterministic local evaluation cases without a live API service."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import tempfile
from typing import Any
from urllib.parse import urlsplit

from app.agent.executor import run_plan
from app.agent.planner import plan_task
from app.database import SessionLocal, init_db
from app.tools.base import ToolResult
from app.tools.defaults import register_default_tools
from app.tools.registry import execute_tool
from app.trace import store
from app.trace.logger import record_tool_result


ROOT = Path(__file__).resolve().parents[2]
CASES_PATH = Path(__file__).with_name("cases.jsonl")
OUTPUT_PATH = ROOT / "workspace" / "eval_outputs" / "eval_report.json"


def load_cases() -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in CASES_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def prepare_runtime() -> None:
    from scripts.init_demo_db import init_demo_db

    init_db()
    register_default_tools()
    init_demo_db()


def _get_path(payload: Any, path: str) -> tuple[bool, Any]:
    """Resolve a small JSON path such as output.pages[0].error."""

    current = payload
    tokens = [token for token in re.split(r"\.|\[|\]", path) if token]
    for token in tokens:
        if isinstance(current, dict) and token in current:
            current = current[token]
            continue
        if isinstance(current, list) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
            continue
        return False, None
    return True, current


def evaluate_metric_assertions(
    payload: dict[str, Any],
    assertions: list[dict[str, Any]] | None,
) -> tuple[bool, list[dict[str, Any]]]:
    """Evaluate declarative structured assertions without custom case code."""

    results: list[dict[str, Any]] = []
    for assertion in assertions or []:
        path = str(assertion.get("path") or "")
        exists, actual = _get_path(payload, path)
        passed = exists
        operator = "exists"
        expected: Any = True
        if "exists" in assertion:
            operator = "exists"
            expected = bool(assertion["exists"])
            passed = exists is expected
        elif "equals" in assertion:
            operator = "equals"
            expected = assertion["equals"]
            passed = exists and actual == expected
        elif "not_equals" in assertion:
            operator = "not_equals"
            expected = assertion["not_equals"]
            passed = exists and actual != expected
        elif "min" in assertion:
            operator = "min"
            expected = assertion["min"]
            passed = exists and isinstance(actual, (int, float)) and actual >= expected
        elif "max" in assertion:
            operator = "max"
            expected = assertion["max"]
            passed = exists and isinstance(actual, (int, float)) and actual <= expected
        elif "contains" in assertion:
            operator = "contains"
            expected = assertion["contains"]
            passed = exists and isinstance(actual, (str, list, tuple, dict)) and expected in actual
        elif "length" in assertion:
            operator = "length"
            expected = assertion["length"]
            passed = exists and hasattr(actual, "__len__") and len(actual) == expected
        results.append(
            {
                "path": path,
                "operator": operator,
                "expected": expected,
                "actual": actual,
                "passed": bool(passed),
            }
        )
    return all(item["passed"] for item in results), results


def _trace_payload(trace) -> dict[str, Any]:
    try:
        output = json.loads(trace.output_json or "{}")
    except json.JSONDecodeError:
        output = {}
    return {
        "trace_id": trace.trace_id,
        "tool_name": trace.tool_name,
        "status": trace.status,
        "output": output,
        "error_message": trace.error_message,
    }


def _case_result(
    case: dict[str, Any],
    *,
    run_id: str,
    status: str,
    payload: dict[str, Any],
    base_passed: bool,
    planned_tools: list[str],
    trace_statuses: dict[str, int],
    trace_count: int,
) -> dict[str, Any]:
    assertions_ok, assertion_results = evaluate_metric_assertions(
        payload,
        case.get("metric_assertions"),
    )
    return {
        "case_id": case["case_id"],
        "category": case.get("category", "uncategorized"),
        "phase": case.get("phase"),
        "network_dependent": bool(case.get("network_dependent")),
        "passed": base_passed and assertions_ok,
        "run_id": run_id,
        "status": status,
        "planned_tools": planned_tools,
        "trace_count": trace_count,
        "trace_statuses": trace_statuses,
        "trace_complete": True,
        "report_exists": False,
        "keyword_matches": [],
        "keywords_ok": True,
        "metric_assertions_ok": assertions_ok,
        "metric_assertions": assertion_results,
    }


def run_task_case(db, case: dict[str, Any]) -> dict[str, Any]:
    hitl_decision = case.get("hitl_decision")
    run = store.create_agent_run(
        db=db,
        task=case["task"],
        report_type=case.get("report_type", "summary"),
        source_mode="mock",
        allowed_tools=case.get("allowed_tools"),
    )
    plan = plan_task(
        case["task"],
        case.get("allowed_tools"),
        "mock",
        planner_mode="deterministic",
    )
    store.update_agent_run_plan(db, run.run_id, plan)
    summary = run_plan(db, run.run_id)

    # ── HITL handling ─────────────────────────────────────────────────
    hitl_seen = summary.get("status") == "waiting_human"
    if hitl_decision and hitl_seen:
        if hitl_decision == "approve":
            # Auto-approve the waiting step
            from datetime import datetime, timezone
            plan_after = json.loads(store.get_agent_run(db, run.run_id).plan_json or "{}")
            pending_step_no = run.current_step + 1
            plan_after["confirmation"] = {
                "required_step_no": pending_step_no,
                "required_tool_name": "file_reader",
                "approved": True,
                "comment": "Auto-approved by eval harness.",
                "approved_at": datetime.now(timezone.utc).isoformat(),
            }
            store.replace_agent_run_plan(db, run.run_id, plan_after)
            store.update_agent_run_status(db, run.run_id, "pending", None)
            summary = run_plan(db, run.run_id)
            hitl_seen = False
        elif hitl_decision == "reject":
            # For reject cases, the run should be in waiting_human state
            pass

    final_run = store.get_agent_run(db, run.run_id)
    traces = store.list_tool_traces(db, run.run_id)
    expected_tools = set(case.get("expected_tools", []))
    traced_tools = {trace.tool_name for trace in traces}
    report_text = ""
    if final_run and final_run.report_path:
        rp = ROOT / final_run.report_path
        if rp.is_file():
            report_text = rp.read_text(encoding="utf-8")
    report_exists = bool(report_text)
    keywords = [str(k).lower() for k in case.get("success_keywords") or []]
    keyword_matches = [k for k in keywords if k.lower() in report_text.lower()]
    keywords_ok = not keywords or bool(keyword_matches)
    expected_status = case.get("expected_status", "completed")
    if hitl_decision == "reject":
        expected_status = "waiting_human"
    passed = (
        summary["status"] == expected_status
        and expected_tools.issubset(traced_tools)
        and (not case.get("report_exists", True) or report_exists)
        and keywords_ok
    )
    assertion_payload = {
        "summary": summary,
        "run": {
            "status": final_run.status if final_run else None,
            "total_tool_calls": final_run.total_tool_calls if final_run else 0,
        },
        "report": {"text": report_text, "exists": report_exists},
        "traces": [_trace_payload(trace) for trace in traces],
    }
    assertions_ok, assertion_results = evaluate_metric_assertions(
        assertion_payload,
        case.get("metric_assertions"),
    )
    passed = passed and assertions_ok
    return {
        "case_id": case["case_id"],
        "category": case.get("category", "uncategorized"),
        "phase": case.get("phase"),
        "network_dependent": bool(case.get("network_dependent")),
        "passed": passed,
        "run_id": run.run_id,
        "status": summary["status"],
        "planned_tools": [step.get("tool_name") for step in plan.get("steps", [])],
        "trace_count": len(traces),
        "trace_statuses": dict(Counter(trace.status for trace in traces)),
        "trace_complete": expected_tools.issubset(traced_tools),
        "report_exists": report_exists,
        "keyword_matches": keyword_matches,
        "keywords_ok": keywords_ok,
        "metric_assertions_ok": assertions_ok,
        "metric_assertions": assertion_results,
    }


def run_direct_tool_case(db, case: dict[str, Any]) -> dict[str, Any]:
    tool_name = case["tool_name"]
    arguments = case.get("arguments") or {}
    run = store.create_agent_run(
        db=db,
        task=f"Evaluation: {case['case_id']}",
        report_type="summary",
        source_mode="mock",
        allowed_tools=[tool_name],
    )
    result = execute_tool(tool_name, arguments)
    trace = record_tool_result(
        db=db,
        run_id=run.run_id,
        step_no=1,
        tool_name=tool_name,
        input_data=arguments,
        result=result,
        latency_ms=0,
    )
    expected_status = case["expected_trace_status"]
    passed = trace.status == expected_status and result.success is case["should_succeed"]
    payload = {
        "success": result.success,
        "output": result.output,
        "metadata": result.metadata,
        "trace": _trace_payload(trace),
    }
    return _case_result(
        case,
        run_id=run.run_id,
        status=trace.status,
        payload=payload,
        base_passed=passed,
        planned_tools=[tool_name],
        trace_statuses={trace.status: 1},
        trace_count=1,
    )


def _run_component_case(
    db,
    case: dict[str, Any],
    component_name: str,
    execute,
) -> dict[str, Any]:
    run = store.create_agent_run(
        db=db,
        task=f"Evaluation: {case['case_id']}",
        report_type="summary",
        source_mode="mock",
        allowed_tools=None,
    )
    output = execute()
    result = ToolResult(
        success=True,
        output=output,
        output_summary=f"{component_name} deterministic evaluation completed.",
        metadata={"eval_mode": case.get("mode"), "phase": case.get("phase")},
    )
    trace = record_tool_result(
        db,
        run.run_id,
        1,
        component_name,
        case.get("arguments") or {},
        result,
        0,
    )
    return _case_result(
        case,
        run_id=run.run_id,
        status=trace.status,
        payload={"success": True, "output": output, "trace": _trace_payload(trace)},
        base_passed=True,
        planned_tools=[component_name],
        trace_statuses={trace.status: 1},
        trace_count=1,
    )


def run_policy_case(db, case: dict[str, Any]) -> dict[str, Any]:
    def execute() -> dict[str, Any]:
        from app.config import settings
        from app.evidence.policy import (
            SourceCandidate,
            classify_tier,
            compute_source_clusters,
            load_source_policy,
            select_sources_by_profile,
        )

        arguments = case.get("arguments") or {}
        policy = load_source_policy(settings.source_policy_path)
        operation = str(arguments.get("operation") or "classify_tier")
        if operation == "classify_tier":
            classification = classify_tier(
                str(arguments.get("source_type") or "web_search"),
                str(arguments.get("uri") or ""),
                dict(arguments.get("metadata") or {}),
                policy,
            )
            return {"classification": asdict(classification), "policy_version": policy.version}

        candidates = []
        for raw in arguments.get("candidates") or []:
            uri = str(raw.get("uri") or "")
            candidates.append(
                SourceCandidate(
                    uri=uri,
                    hostname=str(raw.get("hostname") or (urlsplit(uri).hostname or "")).lower(),
                    organization=raw.get("organization"),
                    title=str(raw.get("title") or uri),
                    snippet=str(raw.get("snippet") or "evidence"),
                    metadata=dict(raw.get("metadata") or {}),
                )
            )
        if operation == "clusters":
            clusters = compute_source_clusters(candidates)
            return {"clusters": clusters, "cluster_count": len(clusters)}
        profile_name = str(arguments.get("profile") or "generic")
        selection = select_sources_by_profile(
            candidates,
            policy.retrieval_profiles[profile_name],
            policy,
            oversample_factor=int(arguments.get("oversample_factor") or 2),
            max_candidates=int(arguments.get("max_candidates") or 15),
        )
        return {"selection": asdict(selection), "policy_version": policy.version}

    return _run_component_case(db, case, "source_policy", execute)


def run_extractor_case(db, case: dict[str, Any]) -> dict[str, Any]:
    def execute() -> dict[str, Any]:
        import hashlib
        import time

        from app.tools.fetch_cache import FetchCache, FetchCacheEntry
        from app.tools.pdf_reader import _extract_pdf
        from app.tools.web_fetcher import (
            _check_pdf_magic,
            _classify_content_basis,
            _extract_body_v2,
            _is_pdf_content_type,
        )

        arguments = case.get("arguments") or {}
        extractor = str(arguments.get("extractor") or "web_html")
        if extractor == "web_html":
            html = str(arguments.get("html") or "")
            max_chars = int(arguments.get("max_chars") or 8000)
            content, method, metadata = _extract_body_v2(
                html,
                str(arguments.get("url") or "https://example.com"),
                trafilatura_enabled=bool(arguments.get("trafilatura_enabled", False)),
            )
            return {
                "content": content[:max_chars],
                "content_length": min(len(content), max_chars),
                "extraction_method": method,
                "content_basis": _classify_content_basis(
                    len(html), len(content), max_chars, None, method
                ),
                "metadata": metadata,
            }
        if extractor == "pdf_detection":
            data = str(arguments.get("magic") or "").encode("latin-1")
            content_type = str(arguments.get("content_type") or "")
            return {
                "magic_match": _check_pdf_magic(data),
                "content_type_match": _is_pdf_content_type(content_type),
            }
        if extractor == "fetch_cache":
            content = str(arguments.get("content") or "cached evidence")
            with tempfile.TemporaryDirectory() as cache_dir:
                cache = FetchCache(cache_dir, default_ttl=int(arguments.get("ttl") or 60))
                params = {"extractor_version": "eval"}
                entry = FetchCacheEntry(
                    cache_key=cache._compute_key("https://example.com", params),
                    url="https://example.com",
                    content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    content=content,
                    content_type="text/html",
                    fetched_at=time.time() - float(arguments.get("age_seconds") or 0),
                    ttl_seconds=int(arguments.get("ttl") or 60),
                    extraction_method="beautifulsoup",
                    extraction_confidence=0.7,
                )
                stored = cache.put(entry)
                loaded, status = cache.lookup("https://example.com", params)
                return {
                    "stored": stored,
                    "cache_status": status,
                    "content": loaded.content if loaded else None,
                    "content_hash": loaded.content_hash if loaded else None,
                }

        import fitz

        if arguments.get("damaged"):
            pdf_bytes = b"%PDF-1.4\ninvalid"
        else:
            document = fitz.open()
            for index, text in enumerate(arguments.get("pages") or ["PDF evidence"]):
                page = document.new_page()
                page.insert_text((72, 72), str(text), fontsize=11)
            pdf_bytes = document.tobytes()
            document.close()
        return _extract_pdf(
            pdf_bytes,
            max_pages=int(arguments.get("max_pages") or 10),
            max_chars=int(arguments.get("max_chars") or 5000),
            ocr_enabled=False,
        )

    return _run_component_case(db, case, "extractor_eval", execute)


def run_reference_case(db, case: dict[str, Any]) -> dict[str, Any]:
    def execute() -> dict[str, Any]:
        from app.evidence.reference_verifier import (
            ReferenceVerificationDetail,
            ReferenceVerificationReport,
            ReferenceVerifier,
            _authors_match,
            _title_similarity,
            extract_academic_references,
            render_reference_verification_section,
        )

        arguments = case.get("arguments") or {}
        operation = str(arguments.get("operation") or "verify")
        if operation == "title_similarity":
            return {"similarity": _title_similarity(arguments.get("a", ""), arguments.get("b", ""))}
        if operation == "authors_match":
            return {"matched": _authors_match(arguments.get("a") or [], arguments.get("b") or [])}
        if operation == "metadata_conflicts":
            verifier = ReferenceVerifier(timeout=1, cache_dir=None)
            conflicts = verifier._detect_metadata_conflicts(
                ref_title=str(arguments.get("ref_title") or ""),
                ref_authors=list(arguments.get("ref_authors") or []),
                ref_year=arguments.get("ref_year"),
                ref_venue=arguments.get("ref_venue"),
                matched_title=arguments.get("matched_title"),
                matched_authors=list(arguments.get("matched_authors") or []),
                matched_year=arguments.get("matched_year"),
                matched_venue=arguments.get("matched_venue"),
            )
            return {"conflicts": conflicts, "conflict_count": len(conflicts)}
        if operation == "synthesize":
            verifier = ReferenceVerifier(timeout=1, cache_dir=None)
            detail = ReferenceVerificationDetail(
                ref_label="REF-EVAL",
                identifier_type="doi",
                identifier_value="10.0000/eval",
                status="unresolved",
            )
            verdict = verifier._synthesize_verdict(
                detail,
                [
                    (
                        str(item.get("index") or "crossref"),
                        str(item.get("status") or "unresolved"),
                        dict(item.get("data") or {}),
                    )
                    for item in arguments.get("results") or []
                ],
            )
            return verdict.to_dict()
        if operation == "extract_academic":
            references = extract_academic_references(arguments.get("provenance") or {})
            return {"references": references, "count": len(references)}
        if operation == "render":
            details = [ReferenceVerificationDetail(**item) for item in arguments.get("details") or []]
            report = ReferenceVerificationReport(
                total=int(arguments.get("total") or len(details)),
                verified=int(arguments.get("verified") or 0),
                probable=int(arguments.get("probable") or 0),
                inconsistent=int(arguments.get("inconsistent") or 0),
                unresolved=int(arguments.get("unresolved") or 0),
                details=details,
            )
            lines = render_reference_verification_section(report)
            return {"lines": lines, "markdown": "\n".join(lines), **report.to_dict()}
        verifier = ReferenceVerifier(timeout=1, cache_dir=None)
        return verifier.verify(arguments.get("references") or []).to_dict()

    return _run_component_case(db, case, "reference_verifier", execute)


def run_retriever_case(db, case: dict[str, Any]) -> dict[str, Any]:
    def execute() -> dict[str, Any]:
        from app.skills.registry import get_skill, init_skill_registry
        from app.tools.crossref_search import _parse_item
        from app.tools.openalex_search import _parse_paper
        from app.tools.registry import get_tool, list_tools

        arguments = case.get("arguments") or {}
        operation = str(arguments.get("operation") or "registry")
        if operation == "parse_openalex":
            return {"paper": _parse_paper(dict(arguments.get("item") or {}))}
        if operation == "parse_crossref":
            return {"paper": _parse_item(dict(arguments.get("item") or {}))}
        if operation == "registry":
            names = sorted(spec.name for spec in list_tools())
            selected = str(arguments.get("tool_name") or "")
            spec = get_tool(selected) if selected else None
            return {
                "tool_names": names,
                "tool_count": len(names),
                "selected_tool": spec.model_dump(mode="json") if spec else None,
            }
        if operation == "skill":
            init_skill_registry(ROOT / "workspace" / "skills")
            skill = get_skill(str(arguments.get("skill_name") or "systematic_review"))
            if skill is None:
                return {"found": False}
            return {
                "found": True,
                "name": skill.name,
                "version": skill.version,
                "required_tools": list(skill.required_tools),
                "step_tools": [step.tool_name for step in skill.steps],
                "step_count": len(skill.steps),
            }
        raise ValueError(f"Unsupported retriever eval operation: {operation}")

    return _run_component_case(db, case, "retriever_eval", execute)


def run_provider_case(db, case: dict[str, Any]) -> dict[str, Any]:
    """Exercise a real provider handler against a deterministic response fixture."""

    from unittest.mock import patch

    class FixtureResponse:
        def __init__(self, body: bytes):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self) -> bytes:
            return self.body

    arguments = case.get("arguments") or {}
    tool_name = str(case.get("tool_name") or "")
    handler_arguments = dict(arguments.get("request") or {})
    response_body = arguments.get("response")

    if tool_name == "arxiv_search":
        from app.tools import arxiv_search

        body = str(response_body or "").encode("utf-8")
        with patch.object(arxiv_search, "urlopen", lambda *_args, **_kwargs: FixtureResponse(body)), patch.object(
            arxiv_search, "_respect_rate_limit", lambda: None
        ):
            result = arxiv_search.arxiv_search_handler(handler_arguments)
    elif tool_name == "semantic_scholar_search":
        from app.tools import semantic_scholar

        body = json.dumps(response_body or {}).encode("utf-8")
        with patch.object(
            semantic_scholar,
            "urlopen",
            lambda *_args, **_kwargs: FixtureResponse(body),
        ):
            result = semantic_scholar.semantic_scholar_handler(handler_arguments)
    elif tool_name == "tavily_search":
        from app.config import Settings
        from app.tools.tavily_search import tavily_search

        body = json.dumps(response_body or {}).encode("utf-8")
        provider_settings = Settings(
            offline_mode=False,
            tavily_api_key="eval-placeholder",
            tavily_search_enabled=True,
            tavily_max_retries=0,
        )
        result = tavily_search(
            handler_arguments,
            settings_obj=provider_settings,
            opener=lambda *_args, **_kwargs: FixtureResponse(body),
            sleeper=lambda _seconds: None,
        )
    else:
        raise ValueError(f"Unsupported provider eval tool: {tool_name}")

    run = store.create_agent_run(
        db=db,
        task=f"Evaluation: {case['case_id']}",
        report_type="summary",
        source_mode="mock",
        allowed_tools=[tool_name],
    )
    trace = record_tool_result(
        db=db,
        run_id=run.run_id,
        step_no=1,
        tool_name=tool_name,
        input_data=handler_arguments,
        result=result,
        latency_ms=0,
    )
    expected_status = str(case.get("expected_trace_status") or "success")
    payload = {
        "success": result.success,
        "output": result.output,
        "metadata": result.metadata,
        "trace": _trace_payload(trace),
    }
    return _case_result(
        case,
        run_id=run.run_id,
        status=trace.status,
        payload=payload,
        base_passed=result.success and trace.status == expected_status,
        planned_tools=[tool_name],
        trace_statuses={trace.status: 1},
        trace_count=1,
    )


def run_case(db, case: dict[str, Any]) -> dict[str, Any]:
    try:
        if case.get("mode") == "direct_tool":
            return run_direct_tool_case(db, case)
        if case.get("mode") == "policy":
            return run_policy_case(db, case)
        if case.get("mode") == "extractor":
            return run_extractor_case(db, case)
        if case.get("mode") == "reference":
            return run_reference_case(db, case)
        if case.get("mode") == "retriever":
            return run_retriever_case(db, case)
        if case.get("mode") == "provider":
            return run_provider_case(db, case)
        return run_task_case(db, case)
    except Exception as exc:
        return {
            "case_id": case.get("case_id", "unknown"),
            "passed": False,
            "status": "failed",
            "failure_reason": str(exc),
        }


def main() -> int:
    prepare_runtime()
    with SessionLocal() as db:
        results = [run_case(db, case) for case in load_cases()]

    passed = sum(1 for result in results if result.get("passed"))
    nd_failed = sum(1 for r in results if not r.get("passed") and r.get("network_dependent"))
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_cases": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "network_skipped": nd_failed,
        "results": results,
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: payload[key] for key in ("total_cases", "passed", "failed")}))
    hard_failed = len(results) - passed - nd_failed
    return 0 if hard_failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
