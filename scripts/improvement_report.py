"""Generate a self-improvement trend report.

Reads improvement_logs, routing_weights, and few_shot_library to produce
a Markdown report showing:
  - Overall score trend
  - Best and regressing strategies
  - Few-shot library status
  - Weight distribution
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import SessionLocal, init_db
from app.improvement.models import ImprovementLog
from sqlalchemy import func, select


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _format_score_change(current: float, previous: float) -> str:
    diff = round(current - previous, 1)
    if diff > 0.2:
        return f"📈 +{diff}"
    elif diff < -0.2:
        return f"📉 {diff}"
    return f"➡ {diff}"


def generate_report() -> str:
    init_db()
    lines: list[str] = []

    lines.append("# Self-Improving 趋势报告")
    lines.append(f"\n生成时间：{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n")

    with SessionLocal() as db:
        # ── Overall stats ──
        total = db.execute(
            select(func.count()).select_from(ImprovementLog)
        ).scalar() or 0
        if total == 0:
            lines.append("暂无改善数据。请先执行几次调研任务。")
            return "\n".join(lines)

        avg_overall = db.execute(
            select(func.avg(ImprovementLog.overall_score))
        ).scalar() or 0.0
        avg_factual = db.execute(
            select(func.avg(ImprovementLog.factual_accuracy))
        ).scalar() or 0.0
        avg_source = db.execute(
            select(func.avg(ImprovementLog.source_quality_score))
        ).scalar() or 0.0
        avg_audit = db.execute(
            select(func.avg(ImprovementLog.auditability_score))
        ).scalar() or 0.0

        lines.append("## 整体概览")
        lines.append(f"| 指标 | 值 |")
        lines.append(f"|---|---|")
        lines.append(f"| 总运行次数 | {total} |")
        lines.append(f"| 平均综合分 | {round(avg_overall, 1)} |")
        lines.append(f"| 平均事实准确性 | {round(avg_factual * 100)}% |")
        lines.append(f"| 平均来源质量 | {round(avg_source, 1)} |")
        lines.append(f"| 平均可审计性 | {round(avg_audit, 1)} |")

        # ── Recent trend (last 10) ──
        recent = (
            db.execute(
                select(ImprovementLog)
                .order_by(ImprovementLog.created_at.desc())
                .limit(10)
            )
            .scalars()
            .all()
        )
        if recent:
            lines.append("\n## 最近 10 次运行")
            lines.append("| 时间 | 类别 | 模式 | 综合分 | 引用 | T0/T1/T2 |")
            lines.append("|---|---|---|---|---|---|")
            for r in reversed(recent):
                ts = r.created_at.strftime("%m-%d %H:%M") if r.created_at else "—"
                tier_str = f"{r.tier_t0}/{r.tier_t1}/{r.tier_t2}"
                lines.append(
                    f"| {ts} | {r.question_category or '—'} | {r.execution_mode or '—'} "
                    f"| {r.overall_score} | {r.citation_count} | {tier_str} |"
                )

        # ── By strategy ──
        strategies = (
            db.execute(
                select(
                    ImprovementLog.skill_composition,
                    ImprovementLog.execution_mode,
                    func.count().label("cnt"),
                    func.avg(ImprovementLog.overall_score).label("avg_score"),
                )
                .group_by(
                    ImprovementLog.skill_composition,
                    ImprovementLog.execution_mode,
                )
                .order_by(func.avg(ImprovementLog.overall_score).desc())
            )
            .all()
        )
        if strategies:
            lines.append("\n## 策略效果排名")
            lines.append("| 策略 | 模式 | 次数 | 平均分 |")
            lines.append("|---|---|---|---|")
            for s in strategies:
                skill = (s.skill_composition or "—")[:40]
                mode = s.execution_mode or "—"
                score = round(s.avg_score, 1) if s.avg_score else 0.0
                lines.append(f"| {skill} | {mode} | {s.cnt} | {score} |")

        # ── By category ──
        categories = (
            db.execute(
                select(
                    ImprovementLog.question_category,
                    func.count().label("cnt"),
                    func.avg(ImprovementLog.overall_score).label("avg_score"),
                )
                .group_by(ImprovementLog.question_category)
                .order_by(func.avg(ImprovementLog.overall_score).desc())
            )
            .all()
        )
        if categories:
            lines.append("\n## 问题类别效果")
            lines.append("| 类别 | 次数 | 平均分 |")
            lines.append("|---|---|---|")
            for c in categories:
                cat = c.question_category or "general"
                score = round(c.avg_score, 1) if c.avg_score else 0.0
                lines.append(f"| {cat} | {c.cnt} | {score} |")

    # ── Routing weights ──
    weights_path = ROOT / "workspace" / "improvement" / "routing_weights.json"
    weights_data = _load_json(weights_path)
    weights = weights_data.get("weights", {})
    if weights:
        lines.append("\n## 路由权重")
        for cat, cat_weights in sorted(weights.items()):
            lines.append(f"\n**{cat}**：")
            for skill, w in sorted(cat_weights.items(), key=lambda x: -x[1]):
                lines.append(f"- {skill}: {w}")

    # ── Few-shot library ──
    library_path = ROOT / "workspace" / "improvement" / "few_shot_library.json"
    library = _load_json(library_path)
    examples = library.get("examples", [])
    lines.append(f"\n## Few-shot 示例库（{len(examples)} 条）")
    if examples:
        for ex in sorted(examples, key=lambda e: -e.get("overall_score", 0)):
            lines.append(
                f"- [{ex.get('category', '—')}] {ex.get('question', '')[:60]}... "
                f"(分: {ex.get('overall_score', 0)}, 步骤: {ex.get('plan_summary', '')[:40]})"
            )
    else:
        lines.append("暂无示例。当综合分 ≥ 7.5 且引用 ≥ 5 时自动晋升。")

    return "\n".join(lines)


if __name__ == "__main__":
    report = generate_report()
    output_path = ROOT / "workspace" / "improvement" / "trend_report.md"
    output_path.write_text(report, encoding="utf-8")
    print(report)
    print(f"\n---\n报告已保存至 {output_path}")