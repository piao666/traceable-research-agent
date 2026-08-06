"""Regression evaluation runner with category breakdown and baseline comparison.

Usage:
    python scripts/run_eval_regression.py                    # Run all, output report
    python scripts/run_eval_regression.py --baseline <json>  # Compare with previous
    python scripts/run_eval_regression.py --category tool_safety  # Filter by category

Outputs:
    workspace/eval_outputs/regression_{timestamp}.json  — full results
    workspace/eval_outputs/regression_{timestamp}.md    — Markdown comparison report
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Fix Windows GBK encoding for emoji output
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.database import SessionLocal
from app.eval.run_eval import load_cases, prepare_runtime, run_case

OUTPUT_DIR = ROOT / "workspace" / "eval_outputs"
BAD_CASES_PATH = ROOT / "docs" / "bad_cases.md"

CATEGORY_LABELS = {
    "tool_safety": "工具安全边界",
    "tool_success": "工具成功路径",
    "report_gen": "报告生成",
    "degradation": "降级与恢复",
    "evidence": "证据链",
    "hitl": "HITL 路径",
    "uncategorized": "未分类",
}


def load_bad_cases() -> list[dict[str, Any]]:
    """Parse docs/bad_cases.md into structured entries.

    Expected format per entry:
        ### BAD-XXX: Title
        - **关联 case_id**: xxx
        - **状态**: known | expected_to_pass
        - **优先级**: high | medium | low
    """
    if not BAD_CASES_PATH.exists():
        return []
    text = BAD_CASES_PATH.read_text(encoding="utf-8")
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in text.splitlines():
        if line.startswith("### BAD-"):
            if current:
                entries.append(current)
            current = {"id": line[4:].strip().split(":")[0].strip(), "title": line[4:].strip()}
        elif current is not None and "关联 case_id" in line:
            current["case_id"] = line.split(":", 1)[-1].strip()
        elif current is not None and "状态" in line:
            current["status"] = line.split(":", 1)[-1].strip()
        elif current is not None and "优先级" in line:
            current["priority"] = line.split(":", 1)[-1].strip()
    if current:
        entries.append(current)
    return entries


def check_bad_cases(results: list[dict[str, Any]], bad_cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cross-reference failed results with bad_cases entries."""
    failed_ids = {r["case_id"] for r in results if not r.get("passed")}
    alerts: list[dict[str, Any]] = []
    for bc in bad_cases:
        bc_case_id = bc.get("case_id", "")
        bc_status = bc.get("status", "")
        if bc_case_id in failed_ids and bc_status == "expected_to_pass":
            alerts.append({
                "bad_case_id": bc.get("id"),
                "title": bc.get("title"),
                "case_id": bc_case_id,
                "expected": "expected_to_pass",
                "actual": "still_failing",
                "priority": bc.get("priority", "unknown"),
            })
        elif bc_case_id not in failed_ids and bc_status == "expected_to_pass":
            alerts.append({
                "bad_case_id": bc.get("id"),
                "title": bc.get("title"),
                "case_id": bc_case_id,
                "expected": "expected_to_pass",
                "actual": "now_passing",
                "priority": bc.get("priority", "unknown"),
            })
    return alerts


def build_category_summary(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Group results by category and compute per-category stats.

    Network-dependent cases that failed are counted as 'skipped' rather than 'failed'.
    """
    by_cat: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in results:
        by_cat[r.get("category", "uncategorized")].append(r)
    summary: dict[str, dict[str, Any]] = {}
    for cat, items in sorted(by_cat.items()):
        passed = sum(1 for i in items if i.get("passed"))
        nd_failed = sum(1 for i in items if not i.get("passed") and i.get("network_dependent"))
        hard_failed = sum(1 for i in items if not i.get("passed") and not i.get("network_dependent"))
        total = len(items)
        # For rate calculation, network-dependent failures don't count against pass rate
        adjusted_total = total - nd_failed
        summary[cat] = {
            "label": CATEGORY_LABELS.get(cat, cat),
            "total": total,
            "passed": passed,
            "failed": hard_failed,
            "skipped_network": nd_failed,
            "rate": round(passed / adjusted_total, 4) if adjusted_total > 0 else 1.0,
            "cases": [i["case_id"] for i in items if not i.get("passed") and not i.get("network_dependent")],
        }
    return summary


def build_markdown_report(
    payload: dict[str, Any],
    baseline: dict[str, Any] | None,
    bad_case_alerts: list[dict[str, Any]],
) -> str:
    """Render a Markdown evaluation regression report."""
    category_summary = payload["category_summary"]
    results = payload["results"]
    total = payload["total_cases"]
    passed = payload["passed"]
    failed = payload["failed"]

    def pct(v: float) -> str:
        return f"{v * 100:.1f}%"

    # Count network-dependent vs hard failures from raw results
    nd_failed = sum(1 for r in results if not r.get("passed") and r.get("network_dependent"))
    hard_failed = sum(1 for r in results if not r.get("passed") and not r.get("network_dependent"))
    effective_total = total - nd_failed

    lines: list[str] = [
        "# Eval Regression Report",
        "",
        f"**Generated**: {payload['generated_at']}",
        f"**Total cases**: {total}",
        f"**Passed**: {passed} ({pct(passed / effective_total) if effective_total else 'N/A'})",
        f"**Failed (hard)**: {hard_failed}",
        f"**Skipped (network)**: {nd_failed}",
        "",
    ]

    # ── Baseline comparison ──────────────────────────────────────────
    if baseline:
        bl_passed = baseline.get("passed", 0)
        bl_total = baseline.get("total_cases", 0)
        regressions = [
            r for r in results
            if not r.get("passed")
            and any(
                br.get("case_id") == r["case_id"] and br.get("passed")
                for br in baseline.get("results", [])
            )
        ]
        fixes = [
            r for r in results
            if r.get("passed")
            and any(
                br.get("case_id") == r["case_id"] and not br.get("passed")
                for br in baseline.get("results", [])
            )
        ]
        lines.extend([
            "## Baseline Comparison",
            "",
            f"* Baseline: `{baseline.get('generated_at', 'unknown')}`",
            f"* Baseline passed: {bl_passed}/{bl_total} ({pct(bl_passed / bl_total) if bl_total else 'N/A'})",
            f"* Regressions (new failures): **{len(regressions)}**",
            f"* Fixes (previously failing, now passing): **{len(fixes)}**",
            "",
        ])
        if regressions:
            lines.append("### Regressions")
            lines.append("")
            lines.append("| case_id | category | baseline | current |")
            lines.append("| --- | --- | --- | --- |")
            for r in regressions:
                lines.append(
                    f"| {r['case_id']} | {r.get('category', '?')} | passed | **failed** |"
                )
            lines.append("")
        if fixes:
            lines.append("### Fixes")
            lines.append("")
            lines.append("| case_id | category |")
            lines.append("| --- | --- |")
            for r in fixes:
                lines.append(f"| {r['case_id']} | {r.get('category', '?')} |")
            lines.append("")
    else:
        lines.extend([
            "## Baseline Comparison",
            "",
            "*No baseline provided. Use `--baseline <previous_report.json>` to compare.*",
            "",
        ])

    # ── Category summary ─────────────────────────────────────────────
    lines.extend([
        "## Category Summary",
        "",
        "| Category | Total | Passed | Failed | Skipped (network) | Rate |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ])
    for cat, stats in sorted(category_summary.items()):
        lines.append(
            f"| {stats['label']} | {stats['total']} | {stats['passed']} | "
            f"{stats['failed']} | {stats.get('skipped_network', 0)} | {pct(stats['rate'])} |"
        )
    lines.append("")

    # ── Failed cases detail ──────────────────────────────────────────
    hard_failed_results = [r for r in results if not r.get("passed") and not r.get("network_dependent")]
    nd_results = [r for r in results if not r.get("passed") and r.get("network_dependent")]
    if nd_results:
        lines.extend([
            "## Network-Dependent Cases (Skipped)",
            "",
            "These cases require external network access and were skipped in this run:",
            "",
            "| case_id | tool | category |",
            "| --- | --- | --- |",
        ])
        for r in nd_results:
            lines.append(
                f"| {r['case_id']} | {r.get('planned_tools', ['?'])[0] if r.get('planned_tools') else '?'} "
                f"| {CATEGORY_LABELS.get(r.get('category', ''), r.get('category', '?'))} |"
            )
        lines.append("")
    if hard_failed_results:
        lines.extend([
            "## Failed Cases",
            "",
        ])
        for r in hard_failed_results:
            lines.extend([
                f"### {r['case_id']}",
                "",
                f"* Category: {CATEGORY_LABELS.get(r.get('category', ''), r.get('category', '?'))}",
                f"* Status: {r.get('status', '?')}",
                f"* Trace count: {r.get('trace_count', 0)}",
                f"* Report exists: {r.get('report_exists', False)}",
                f"* Keywords OK: {r.get('keywords_ok', True)}",
                f"* Keyword matches: {r.get('keyword_matches', [])}",
                "",
            ])
            if r.get("failure_reason"):
                lines.append(f"* Failure: {r['failure_reason']}")
                lines.append("")

    # ── Bad cases check ──────────────────────────────────────────────
    if bad_case_alerts:
        lines.extend([
            "## Bad Cases Check",
            "",
        ])
        still_failing = [a for a in bad_case_alerts if a["actual"] == "still_failing"]
        now_passing = [a for a in bad_case_alerts if a["actual"] == "now_passing"]
        if still_failing:
            lines.append("### Still Failing (expected to pass)")
            lines.append("")
            lines.append("| Bad Case | case_id | Priority |")
            lines.append("| --- | --- | --- |")
            for a in still_failing:
                lines.append(
                    f"| **{a['title']}** | {a['case_id']} | {a['priority']} |"
                )
            lines.append("")
        if now_passing:
            lines.append("### Now Passing")
            lines.append("")
            lines.append("| Bad Case | case_id |")
            lines.append("| --- | --- |")
            for a in now_passing:
                lines.append(f"| {a['title']} | {a['case_id']} |")
            lines.append("")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Eval regression runner")
    parser.add_argument(
        "--baseline", type=str, default=None,
        help="Path to previous regression JSON for comparison",
    )
    parser.add_argument(
        "--category", "-c", type=str, default=None,
        help="Filter by category (tool_safety, tool_success, report_gen, degradation, evidence, hitl)",
    )
    args = parser.parse_args()

    # ── Run ──────────────────────────────────────────────────────────
    prepare_runtime()
    all_cases = load_cases()
    cases = [c for c in all_cases if not args.category or c.get("category") == args.category]

    with SessionLocal() as db:
        results = [run_case(db, case) for case in cases]

    passed = sum(1 for r in results if r.get("passed"))
    nd_failed = sum(1 for r in results if not r.get("passed") and r.get("network_dependent"))
    hard_failed = sum(1 for r in results if not r.get("passed") and not r.get("network_dependent"))
    failed = hard_failed
    category_summary = build_category_summary(results)

    # ── Baseline ─────────────────────────────────────────────────────
    baseline: dict[str, Any] | None = None
    if args.baseline:
        bl_path = Path(args.baseline)
        if bl_path.is_file():
            baseline = json.loads(bl_path.read_text(encoding="utf-8"))

    # ── Bad cases ────────────────────────────────────────────────────
    bad_cases = load_bad_cases()
    bad_case_alerts = check_bad_cases(results, bad_cases)

    # ── Output ───────────────────────────────────────────────────────
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_cases": len(results),
        "passed": passed,
        "failed": failed,
        "category_filter": args.category,
        "category_summary": {
            cat: {k: v for k, v in stats.items() if k != "cases"}
            for cat, stats in category_summary.items()
        },
        "bad_case_alerts": bad_case_alerts,
        "results": results,
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = OUTPUT_DIR / f"regression_{timestamp}.json"
    md_path = OUTPUT_DIR / f"regression_{timestamp}.md"

    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_report = build_markdown_report(payload, baseline, bad_case_alerts)
    md_path.write_text(md_report, encoding="utf-8")

    # ── Console summary ──────────────────────────────────────────────
    nd = sum(1 for r in results if not r.get("passed") and r.get("network_dependent"))
    hf = sum(1 for r in results if not r.get("passed") and not r.get("network_dependent"))
    print(f"\n  Total: {len(results)}  Passed: {passed}  Failed (hard): {hf}  Skipped (network): {nd}")
    for cat, stats in sorted(category_summary.items()):
        snd = stats.get("skipped_network", 0)
        if stats["failed"] == 0 and snd == 0:
            icon = "✅"
        elif stats["failed"] == 0 and snd > 0:
            icon = "⚠️"
        else:
            icon = "❌"
        extra = f" ({snd} network)" if snd > 0 else ""
        print(f"  {icon} {stats['label']}: {stats['passed']}/{stats['total']}{extra}")
    if bad_case_alerts:
        still = [a for a in bad_case_alerts if a["actual"] == "still_failing"]
        if still:
            print(f"\n  ⚠️  {len(still)} bad case(s) still failing (expected to pass):")
            for a in still:
                print(f"     - {a['case_id']}: {a['title']}")
    print(f"\n  Report: {md_path}")
    print(f"  JSON:   {json_path}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
