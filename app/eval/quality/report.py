"""Markdown report generation for research quality evaluation."""

from __future__ import annotations

from app.eval.quality.metrics import QualityEvalSummary, ResearchQualityReport


def render_quality_report(summary: QualityEvalSummary) -> str:
    """Render a Markdown quality evaluation report."""

    lines: list[str] = [
        "# 调研质量评测报告",
        "",
        f"## 总览",
        "",
        f"| 指标 | 数值 |",
        f"|------|------|",
        f"| 总题数 | {summary.total_questions} |",
        f"| 综合平均分 | {summary.avg_overall}/10 |",
        f"| 相关性平均分 | {summary.avg_relevance}/10 |",
        f"| 事实准确性 | {summary.avg_factual_accuracy * 100:.0f}% claims verified |",
        f"| 覆盖度平均分 | {summary.avg_coverage}/10 |",
        f"| 来源质量 | 独立来源:{summary.total_independent_sources}，唯一资源:{summary.total_unique_resources}，未解决冲突:{summary.total_unresolved_conflicts} |",
        f"| 可审计性 | 平均 {summary.total_citations // max(summary.total_questions, 1)} 引用/报告, 准确率 {summary.avg_citation_accuracy * 100:.0f}% |",
        "",
        "## 逐题明细",
        "",
    ]

    for i, report in enumerate(summary.reports, start=1):
        lines.extend(_render_question_detail(i, report))

    # ── Improvement suggestions ─────────────────────────────────────
    lines.extend([
        "## 改进建议",
        "",
    ])

    if summary.avg_coverage < 7.0:
        lines.append("1. **覆盖度瓶颈**：多个题目的覆盖度评分偏低，建议增加子查询分解或使用 `academic_literature` profile 补充学术来源。")
    if summary.avg_factual_accuracy < 0.8:
        lines.append("2. **事实准确性**：部分 claims 缺少引用支撑，建议启用 `citation_validation_llm_enabled=true` 进行 LLM 二次判定。")
    if summary.avg_auditability < 7.0:
        lines.append("3. **可审计性**：引用数量或全文占比偏低，建议增加 `web_fetcher` 步骤获取更多全文证据。")

    if summary.total_unresolved_conflicts:
        lines.append("4. **来源质量**：仍有未解决冲突，建议围绕争议 Claim 补充独立证据。")

    if not lines[-1].startswith(("1.", "2.", "3.", "4.")):
        lines.append("当前各项指标均在合理范围内，继续保持。")

    return "\n".join(lines)


def _render_question_detail(index: int, report: ResearchQualityReport) -> list[str]:
    """Render a single question's quality detail."""
    return [
        f"### {index}. {report.question[:80]}{'...' if len(report.question) > 80 else ''}",
        "",
        f"| 维度 | 得分 | 详情 |",
        f"|------|------|------|",
        f"| 相关性 | {report.relevance_score}/10 | {report.relevance_rationale} |",
        f"| 事实准确性 | {report.factual_accuracy * 100:.0f}% | {report.verified_claims}/{report.verified_claims + report.unsupported_claims} claims verified |",
        f"| 覆盖度 | {report.coverage_score}/10 | 覆盖: {', '.join(report.covered_dimensions) if report.covered_dimensions else '核心主题'} |",
        f"| 来源质量 | {report.source_quality_score}/10 | Claim覆盖:{report.claim_support_coverage:.0%}，独立来源:{report.independent_source_count} |",
        f"| 可审计性 | {report.auditability_score}/10 | {report.citation_count} 引用, 准确率 {report.citation_accuracy * 100:.0f}% |",
        f"| **综合** | **{report.overall_score}/10** | |",
        "",
    ]
