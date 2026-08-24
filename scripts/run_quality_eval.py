# -*- coding: utf-8 -*-
"""Quality evaluation runner — thin wrapper for CI integration.

Usage:
    python scripts/run_quality_eval.py                    # mock mode (CI)
    python scripts/run_quality_eval.py --mode real        # real mode (local)
    python scripts/run_quality_eval.py --dataset all      # all datasets
    python scripts/run_quality_eval.py --dataset research # research only
"""
import sys, json, argparse
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.eval.quality.runner import run_dataset, load_dataset
from app.eval.quality.report import render_quality_report
from app.eval.quality.metrics import QualityEvalSummary
from app.llm.providers import create_llm_client
from app.config import settings
from datetime import datetime, timezone

parser = argparse.ArgumentParser(description="Quality evaluation runner")
parser.add_argument("--mode", default="mock", choices=["mock", "real"])
parser.add_argument("--dataset", default="research_questions", help="research_questions, fact_check, comparison, all")
args = parser.parse_args()

llm = create_llm_client(settings) if args.mode == "real" else None
if args.mode == "mock":
    print("Mode: mock (offline, no LLM judge)")
else:
    print(f"Mode: real (LLM: {settings.llm_provider})")

datasets = ["research_questions", "fact_check", "comparison"] if args.dataset == "all" else [args.dataset]
all_reports = []

for ds in datasets:
    try:
        questions = load_dataset(ds)
    except FileNotFoundError:
        print(f"  Dataset '{ds}' not found, skipping")
        continue
    
    reports = []
    for item in questions:
        print(f"  [{item['id']}] {item['question'][:60]}...")
        from app.eval.quality.runner import run_quality_eval
        r = run_quality_eval(
            question=item["question"],
            report_type=item.get("report_type", "summary"),
            source_mode=args.mode,
            skill_name=item.get("skill", "hybrid_research"),
            retrieval_profile=item.get("retrieval_profile", "evaluation"),
            llm_client=llm,
        )
        reports.append(r)
    
    n = len(reports)
    summary = QualityEvalSummary(
        total_questions=n,
        avg_overall=round(sum(r.overall_score for r in reports) / n, 1) if n else 0,
        avg_relevance=round(sum(r.relevance_score for r in reports) / n, 1) if n else 0,
        avg_factual_accuracy=round(sum(r.factual_accuracy for r in reports) / n, 2) if n else 0,
        avg_coverage=round(sum(r.coverage_score for r in reports) / n, 1) if n else 0,
        avg_source_quality=round(sum(r.source_quality_score for r in reports) / n, 1) if n else 0,
        avg_auditability=round(sum(r.auditability_score for r in reports) / n, 1) if n else 0,
        overall_t0_count=sum(r.t0_count for r in reports),
        overall_t1_count=sum(r.t1_count for r in reports),
        overall_t2_count=sum(r.t2_count for r in reports),
        total_citations=sum(r.citation_count for r in reports),
        avg_citation_accuracy=round(sum(r.citation_accuracy for r in reports) / n, 2) if n else 0,
        reports=reports,
    )
    all_reports.append((ds, summary))
    
    md = render_quality_report(summary)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = ROOT / "workspace" / "eval_outputs" / f"quality_{ds}_{ts}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    
    print(f"  {ds}: overall={summary.avg_overall} citations={summary.total_citations}")

# Final summary
print(f"\nDone. Reports: workspace/eval_outputs/")
for ds, s in all_reports:
    print(f"  {ds}: overall={s.avg_overall}/10 citations={s.total_citations}")

# Exit code: 0 if all passed, 1 if any dataset below threshold
threshold = 3.0 if args.mode == "mock" else 5.0
all_pass = all(s.avg_overall >= threshold for _, s in all_reports)
sys.exit(0 if all_pass else 1)
