import { Link } from "react-router-dom";
import type { TaskPlanResponse } from "../api/client";
import { basisLabel, safeExternalUrl } from "../lib/evidence";
import { Panel, StatusChip } from "./primitives";

const reasons: Record<string, string> = {
  auth_error: "认证失败", unavailable: "提供方不可用", capability_unavailable: "配置或能力不可用",
  tool_call_limit: "该工具调用额度耗尽", cooldown: "冷却中", input_blocked: "相同输入被拦截，其他输入仍可能可用",
  rate_limited: "服务限流", timeout: "请求超时", provider_error: "提供方暂时异常",
  tool_calls: "总工具调用额度耗尽", llm_calls: "总模型调用额度耗尽", tokens: "总 Token 额度不足",
  deadline: "总执行时限已到", estimated_cost: "费用估值额度不足",
  tool_price_unconfigured: "费用上限已启用，但工具价格估值未配置",
  llm_price_unconfigured: "费用上限已启用，但模型价格估值未配置",
  parent_cancelled: "父任务已取消", parent_terminal: "父任务已结束",
};
const recoveryReason = (reason?: string | null) => reason ? reasons[reason] || `其他原因（${reason}）` : "无全工具阻塞记录";
const date = (value: number) => Number.isFinite(value) ? new Date(value * 1000).toLocaleString("zh-CN") : "未记录";

export function ExecutionInsights({ plan }: { plan: TaskPlanResponse | null }) {
  const insights = plan?.execution_insights;
  const budget = plan?.execution_budget;
  return <div className="stack r8-insights">
    <Panel title="共享执行预算">
      {!budget ? <p>没有可用预算账本。可能尚未执行或属于旧任务；不等于剩余额度无限。{!plan && "计划读取失败，请使用“刷新状态”重试。"}</p> : <>
        <p>父任务与深化子任务共用额度。{budget.root_run_id !== plan?.run_id ? <Link className="source-link" to={`/runs/${encodeURIComponent(budget.root_run_id)}`}>查看预算所属父任务</Link> : "本任务是预算所属根任务。"}</p>
        <dl className="r5-facts">
          <dt>工具调用（已用 / 上限）</dt><dd>{budget.tool_calls} / {budget.limits.max_tool_calls}</dd>
          <dt>模型调用（已用 / 上限）</dt><dd>{budget.llm_calls} / {budget.limits.max_llm_calls}</dd>
          <dt>Token 记账（含预留 / 上限）</dt><dd>{budget.accounted_tokens} / {budget.limits.max_tokens}</dd>
          <dt>执行截止时间</dt><dd>{date(budget.deadline)}</dd>
          <dt>费用估值（{budget.cost_currency}）</dt><dd>{budget.cost_evaluable ? budget.estimated_cost.toFixed(4) : "不可完整估算（存在未配置价格）"}</dd>
          <dt>费用上限</dt><dd>{budget.limits.max_estimated_cost > 0 ? `${budget.limits.max_estimated_cost} ${budget.cost_currency}` : "未启用（不是零费用）"}</dd>
        </dl>
        {budget.stop_reason && <p className="warning-banner">预算停止原因：{recoveryReason(budget.stop_reason)}。已有 Trace 和材料保留，不能用中间报告冒充最终报告。</p>}
        <p>审批恢复不重置额度或截止时间；工具人工确认等待也计时。完整重试创建新 Run 和新账本，可能再次产生费用。</p>
        <details><summary>预算口径与限制</summary><p>工具次数按调用记账，不是每个 HTTP 请求或 URL。Token 缺少 usage 时保留预留值；费用是部署配置的保守估值，不是账单金额。截止时间阻止新操作，不强杀已发出的请求。</p><p>此账本不包含草稿规划、独立工具 API 或运行结束后单独发起的记忆提取。</p></details>
      </>}
    </Panel>
    <Panel title="工具恢复与许可名单">
      {!insights ? <p>执行说明暂不可用，不能据此判断所有工具正常；请刷新状态或查看 Trace。旧版本 API 可能没有此字段。</p> : <>
        <p>以下为 {date(insights.sampled_at)} 的后端快照；“可继续选择”不是外部服务连通性验证。</p>
        {!insights.allowed_tools.length && <p className="warning-banner">当前许可名单为空，不允许执行任何工具。</p>}
        {!insights.recovery_recorded && <p>未记录 ReAct 恢复状态；静态计划或历史任务不能据此推断已自动切换路径。</p>}
        <ul className="r8-tool-list">{insights.tools.map((tool) => <li key={tool.name}>
          <div className="run-actions"><strong>{tool.name}</strong><StatusChip tone={tool.status === "available" || tool.status === "unknown" ? "neutral" : "warning"}>{{ available: "可继续选择", disabled: "本任务内禁用", exhausted: "单工具额度耗尽", cooldown: "冷却中", unknown: "恢复状态未记录" }[tool.status] || "未知状态"}</StatusChip></div>
          <p>{recoveryReason(tool.reason)}；实际尝试 {tool.attempts ?? "未记录"}，剩余尝试 {tool.remaining_attempts ?? "未记录"}。</p>
          {tool.blocked_input_count > 0 && <p>已拦截 {tool.blocked_input_count} 组重复输入，不表示整个工具被禁用。</p>}
          {tool.retry_at && <p>冷却至 {date(tool.retry_at)}；到期只恢复选择资格，不保证自动重试或请求成功。</p>}
        </li>)}</ul>
        <p>单工具失败不等于整个任务必须失败。只有仍有获准路径、剩余预算与可用证据时，研究才可能继续；最终状态以上方 Run 状态为准。</p>
      </>}
    </Panel>
    <Panel title="候选来源与抓取进度">
      {!insights ? <p>尚未取得来源队列，不能显示为零来源。</p> : <>
        <p>待抓取 {insights.source_context.gaps.pending_fetch} · 已抓取 {insights.source_context.gaps.fetched} · 抓取失败 {insights.source_context.gaps.failed_fetch} · 非全文 {insights.source_context.gaps.full_text_missing}</p>
        <p>候选 URL 不等于有效证据；抓取成功也不保证内容完整或事实正确。队列只保留有限候选，当前证据与报告引用请到“证据追踪”核对。</p>
        {insights.source_context.omitted_count > 0 && <p>队列限长，存在未展示或已被替换的候选；完整工具输出保留在 Trace。</p>}
        {!insights.source_context.sources.length && <p>当前队列没有可展示的网页候选；这不等于没有本地文件或 SQL 证据。</p>}
        <div className="stack">{insights.source_context.sources.map((source) => {
          const url = safeExternalUrl(source.url);
          return <details key={source.source_id} className="evidence-card"><summary>{source.title || "未命名来源"} · {{ pending: "待抓取", fetched: "已抓取", failed: "抓取失败" }[source.fetch_status] || "状态未记录"}</summary>
            <p>{basisLabel(source.content_basis)} · 抓取尝试 {source.fetch_attempts}</p>
            {url ? <a className="source-link" href={url} target="_blank" rel="noopener noreferrer">{source.url}（新窗口）</a> : <p>来源 URL 不安全，不提供打开链接。</p>}
            <blockquote>{source.snippet || "尚无片段"}</blockquote>
            {source.run_ids.length === 1 && source.trace_ids.map((id) => <p key={id}><Link className="source-link" to={`/runs/${encodeURIComponent(source.run_ids[0])}?trace=${encodeURIComponent(id)}`}>查看候选 Trace {id}</Link></p>)}
          </details>;
        })}</div>
      </>}
    </Panel>
  </div>;
}
