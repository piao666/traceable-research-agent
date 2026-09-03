import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import { useRunContext } from "../hooks/useRunContext";
import { Button, Panel, StatusChip } from "../components/primitives";
import { useEvidence } from "../hooks/useEvidence";
import { useFocusTarget } from "../hooks/useFocusTarget";
import { basisLabel, citationTargets, safeExternalUrl } from "../lib/evidence";

function SourceLink({ url, title }: { url: string; title?: string }) {
  const safe = safeExternalUrl(url);
  return safe ? <a className="source-link" href={safe} aria-label={`${title || url}（新窗口）`} target="_blank" rel="noopener noreferrer">{title || url} ↗</a> : <span>{title || url || "来源未记录"}</span>;
}

export function EvidencePage() {
  const { task } = useRunContext();
  const runId = task!.run_id;
  const data = useEvidence(runId, task!.updated_at);
  const targets = useMemo(() => citationTargets(data.provenance), [data.provenance]);
  const [params] = useSearchParams();
  const selected = params.get("citation");
  const [filter, setFilter] = useState("");
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState("");
  useFocusTarget(selected ? `citation-${selected}` : null, !data.loading);
  const items = data.bundle?.evidence_items.filter((item) => `${item.title} ${item.source_ref} ${item.snippet}`.toLowerCase().includes(filter.toLowerCase())) ?? [];
  async function download() {
    setExporting(true); setExportError("");
    try { await api.downloadEvidence(runId); } catch (reason) { setExportError(errorMessage(reason)); }
    finally { setExporting(false); }
  }
  return <div className="stack">
    <div className="section-heading"><h2>证据追踪</h2><div className="run-actions"><Button variant="secondary" onClick={data.refresh}>刷新证据</Button><Button disabled={!data.bundle || data.loading || !!data.error} loading={exporting} onClick={download}>导出来源与片段 JSON</Button></div></div>
    {exportError && <div className="error-banner" role="alert">{exportError}</div>}
    {data.loading && <p role="status">正在读取持久化证据…</p>}
    {data.error && <div className="error-banner" role="alert">证据读取失败：{data.error}。这不等于零证据。</div>}
    {data.provenanceError && <div className="warning-banner" role="alert">引用图谱不可用：{data.provenanceError}。可以查看来源，但不能确认报告引用对应关系。</div>}
    {data.bundle?.warnings.map((warning) => <div className="warning-banner" key={warning}>{warning}</div>)}
    {selected && !data.loading && !targets.has(selected) && <div className="error-banner" role="alert">找不到引用 {selected} 的对应记录；不会自动关联其他编号。</div>}
    <Panel title="报告引用 → 原始证据片段">
      <p>以下为持久化引用关系，关联存在不代表结论已被事实核实。正文与结论需人工对照。</p>
      {!targets.size && !data.loading && <p className="empty-state">没有可展示的引用关系，不能评估引用支持情况。</p>}
      <div className="stack">{[...targets.values()].map((target) => <article className={`evidence-card${selected === target.label ? " selected-evidence" : ""}`} id={`citation-${target.label}`} tabIndex={-1} key={target.label}>
        <div className="section-heading"><h3>{target.label}</h3><StatusChip tone={target.resolved ? "neutral" : "danger"}>{target.resolved ? "关联可解析" : "关联不完整"}</StatusChip></div>
        <p><strong>{target.origin === "source_excerpt" ? "来源摘录：" : "结论："}</strong>{target.claim || "结论记录缺失"}</p>
        {target.origin === "source_excerpt" && <p>此条为来源摘录，不是对计划目标或综合结论的独立事实核查。</p>}
        <blockquote>{target.text || "原始片段缺失，不能视作支持证据"}</blockquote>
        <p>{target.basis} · 关系：{target.relation || "未标注"}</p>
        <SourceLink url={target.url} title={target.source} /><p className="source-uri">{target.url}</p>
        <p className="source-uri">Passage：{target.passageId}</p>
        <p className="source-uri">Snapshot：{target.snapshotId || "未记录"} · Trace：{target.traceId || "未记录"}</p>
        {target.traceId && <Link className="source-link" to={`/runs/${runId}?trace=${encodeURIComponent(target.traceId)}`}>查看来源 Trace</Link>}
      </article>)}</div>
    </Panel>
    <Panel title={`来源与片段${data.bundle ? ` · ${data.bundle.total_evidence_items} 条证据` : ""}`}>
      <p>条目数不等于独立来源数；搜索摘要与抓取全文分别标识。空结果和内部审计请在工作台查看。</p>
      <label className="field">筛选来源或片段<input className="input" value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="标题、URL 或片段关键词" /></label>
      {!items.length && !data.loading && data.bundle && <p className="empty-state">{filter ? "没有匹配的证据条目。" : "当前没有有效来源证据，不能据此生成受支持的结论。"}</p>}
      <div className="stack section-gap">{items.map((item) => <article className="evidence-card" key={item.evidence_id}>
        <h3>{item.evidence_id} · {item.title}</h3>
        <div className="run-actions"><StatusChip>{basisLabel(item.metadata.content_basis)}</StatusChip><span>{item.tool_name} · {item.source_type}</span>{item.is_mock && <StatusChip tone="warning">模拟数据</StatusChip>}{item.is_fallback && <StatusChip tone="warning">降级来源</StatusChip>}</div>
        {item.unsupported_reason && <p className="error-banner">不支持原因：{item.unsupported_reason}</p>}
        <blockquote>{item.snippet}</blockquote><SourceLink url={item.source_ref || ""} />
        {item.trace_id && <p><Link className="source-link" to={`/runs/${runId}?trace=${encodeURIComponent(item.trace_id)}`}>查看来源 Trace</Link></p>}
      </article>)}</div>
    </Panel>
  </div>;
}
