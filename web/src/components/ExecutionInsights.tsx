import { Link } from "react-router-dom";
import type { TaskPlanResponse } from "../api/client";
import { basisLabel, safeExternalUrl } from "../lib/evidence";
import { Panel } from "./primitives";

export function ExecutionInsights({ plan }: { plan: TaskPlanResponse | null }) {
  const insights = plan?.execution_insights;
  return <div className="stack r8-insights">
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
