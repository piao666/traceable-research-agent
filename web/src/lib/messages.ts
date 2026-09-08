const qualityWarningTranslations: Record<string, string> = {
  "Demonstration/fallback sources are not verified live research.": "演示或回退来源未经过真实联网研究验证。",
  "The requested result was not established; available text is not proof of goal completion.": "尚未取得请求的研究结果；现有文本不能证明研究目标已经完成。",
  "Some research steps failed or were skipped; inspect the persisted Trace before using the report.": "部分研究步骤执行失败或被跳过；使用报告前请检查已保存的 Trace。",
  "Some source pages/documents could not be read; only successfully extracted content supports this report.": "部分来源页面或文档无法读取；本报告仅由成功提取的内容提供支持。",
  "ReAct stopped with a limitation; the available evidence does not imply exhaustive research.": "ReAct 因执行限制而停止；现有证据不能代表已经完成全面研究。",
  "ReAct upgrade/fallback did not complete as requested.": "ReAct 升级或回退流程未按预期完成。",
  "Historical result predates current integrity or trace-to-source mapping checks; re-run before relying on its quality metrics.": "该历史结果生成于当前完整性及 Trace 来源映射检查之前；依赖其质量指标前请重新运行。",
  "Further deepening skipped to preserve final-report budget; completeness is not established.": "为保留最终报告预算，已跳过进一步深化；当前研究完整性尚未得到确认。",
  "Deepening synthesis failed; research completeness was not established.": "深化综合失败；当前研究完整性尚未得到确认。",
  "Invalid deepening response; research completeness was not established.": "深化响应无效；当前研究完整性尚未得到确认。",
  "Follow-up learnings are exploratory notes; only cited parent-run passages support the final report. See linked sub-runs for their evidence.": "后续学习内容仅为探索性记录；最终报告只能由父任务中已引用的片段提供支持。相关证据请查看关联的子任务。",
  "Some follow-up runs did not complete; deepening is limited.": "部分后续任务未完成，本次深化研究存在限制。",
  "Optional adaptive ReAct upgrade is unavailable; planned research remains available.": "可选的自适应 ReAct 升级当前不可用；仍可继续执行既定研究计划。",
  "Offline mode uses demonstration sources, not live external research.": "离线模式使用演示来源，不属于真实外部联网研究。",
};

const taskFieldsPrefix = "Clarify these task fields before retrying:";

export function localizeQualityWarning(warning: string): string {
  const normalized = warning.trim();
  if (normalized.startsWith(taskFieldsPrefix)) {
    const fields = normalized.slice(taskFieldsPrefix.length).trim();
    return `重试前请先明确以下任务字段：${fields || "未记录"}。`;
  }
  return qualityWarningTranslations[normalized] ?? normalized;
}
