export function ResearchPolicyNote({ sourceMode, executionMode }: { sourceMode?: string | null; executionMode?: string | null }) {
  return <aside className="summary-callout" aria-label="研究执行边界">
    <p>深度 Web 不限于 GitHub：还可使用获准工具检索官网、论文和技术文档。GitHub 是可选来源，不保证每种来源都可用。</p>
    <p>{executionMode === "react" ? "ReAct 在单工具失败、冷却或额度用尽后，可在许可名单与剩余总预算内选择其他路径。" : "Planned 按已批准步骤执行，不保证自动改选工具；动态恢复由 ReAct 承担。"} 必需步骤失败、证据不足或总预算耗尽仍可能终止任务。</p>
    <p>{sourceMode === "real" ? "真实研究禁止切换到 mock／offline 或使用模拟结果补齐证据。" : sourceMode === "mock" || sourceMode === "offline" ? "此任务标记为模拟／离线模式，不能作为真实联网研究验收。" : "来源模式未记录，请核对部署与任务配置；不能推断为真实联网研究。"} 恢复不会扩大工具权限，也不能绕过人工确认。</p>
  </aside>;
}
