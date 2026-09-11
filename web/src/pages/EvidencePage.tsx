import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import { useRunContext } from "../hooks/useRunContext";
import { Button, Panel, StatusChip } from "../components/primitives";
import { useEvidence } from "../hooks/useEvidence";
import { useFocusTarget } from "../hooks/useFocusTarget";
import { basisLabel, citationTargets, safeExternalUrl, textValue } from "../lib/evidence";

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
  const items = useMemo(() => {
    if (!data.provenance) return [];
    const snapshots = new Map(data.provenance.source_snapshots.map((item) => [textValue(item.snapshot_id), item]));
    const documents = new Map(data.provenance.source_documents.map((item) => [textValue(item.document_id), item]));
    return data.provenance.passages.flatMap((passage) => {
      const snapshot = snapshots.get(textValue(passage.snapshot_id));
      const document = documents.get(textValue(snapshot?.document_id));
      const item = {
        passageId: textValue(passage.passage_id),
        title: textValue(document?.title) || "来源标题未记录",
        url: textValue(document?.canonical_uri),
        text: textValue(passage.text),
        basis: basisLabel(passage.content_basis),
        traceId: textValue(passage.origin_trace_id || passage.trace_id),
        originRunId: textValue(passage.origin_run_id) || runId,
        researchNodeId: textValue(passage.research_node_id),
      };
      const searchable = `${item.title} ${item.url} ${item.text}`.toLowerCase();
      return searchable.includes(filter.toLowerCase()) ? [item] : [];
    });
  }, [data.provenance, filter, runId]);
  async function download() {
    setExporting(true); setExportError("");
    try { await api.downloadEvidence(runId); } catch (reason) { setExportError(errorMessage(reason)); }
    finally { setExporting(false); }
  }
  return <div className="stack">
    <div className="section-heading"><h2>证据追踪</h2><div className="run-actions"><Button variant="secondary" onClick={data.refresh}>刷新证据</Button><Button disabled={!data.provenance || data.loading || !!data.error} loading={exporting} onClick={download}>导出来源与片段 JSON</Button></div></div>
    {exportError && <div className="error-banner" role="alert">{exportError}</div>}
    {data.loading && <p role="status">正在读取持久化证据…</p>}
    {data.error && <div className="error-banner" role="alert">完整研究证据读取失败：{data.error}。这不等于零证据。</div>}
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
        {(target.originTraceId || target.traceId) && <Link className="source-link" to={`/runs/${encodeURIComponent(target.originRunId ?? runId)}?trace=${encodeURIComponent(target.originTraceId ?? target.traceId)}`}>查看来源 Trace</Link>}
      </article>)}</div>
    </Panel>
    <Panel title={`来源与片段${data.provenance ? ` · ${data.provenance.passages.length} 条证据` : ""}`}>
      <p>条目数不等于独立来源数；搜索摘要与抓取全文分别标识。空结果和内部审计请在工作台查看。</p>
      <label className="field">筛选来源或片段<input className="input" value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="标题、URL 或片段关键词" /></label>
      {!items.length && !data.loading && data.provenance && <p className="empty-state">{filter ? "没有匹配的证据条目。" : "当前没有有效来源证据，不能据此生成受支持的结论。"}</p>}
      <div className="stack section-gap">{items.map((item) => <article className="evidence-card" key={item.passageId}>
        <h3>{item.passageId} · {item.title}</h3>
        <div className="run-actions"><StatusChip>{item.basis}</StatusChip>{item.researchNodeId && <span>研究节点：{item.researchNodeId}</span>}</div>
        <blockquote>{item.text}</blockquote><SourceLink url={item.url} />
        {item.traceId && <p><Link className="source-link" to={`/runs/${encodeURIComponent(item.originRunId)}?trace=${encodeURIComponent(item.traceId)}`}>查看来源 Trace</Link></p>}
      </article>)}</div>
    </Panel>
  </div>;
}
