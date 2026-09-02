import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, errorMessage, type ReportResponse, type ProvenanceBundleResponse } from "../api/client";
import { useRunContext } from "../hooks/useRunContext";
import { SafeMarkdown } from "../components/SafeMarkdown";
import { Button, Panel } from "../components/primitives";
import { citationTargets } from "../lib/evidence";

export function ReportPage() {
  const { task } = useRunContext();
  const runId = task!.run_id, revision = task!.updated_at;
  const [report, setReport] = useState<ReportResponse | null>(null);
  const [graph, setGraph] = useState<ProvenanceBundleResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [graphError, setGraphError] = useState("");
  const [retry, setRetry] = useState(0);
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState("");
  const targets = useMemo(() => citationTargets(graph), [graph]);
  useEffect(() => {
    const controller = new AbortController(); let active = true;
    setLoading(true); setError(""); setGraphError(""); setReport(null); setGraph(null);
    void api.getReport(runId, controller.signal).then(async (result) => {
      if (!active) return;
      setReport(result); setGraph(null);
      if (result.exists) {
        try { const graph = await api.getProvenance(runId, controller.signal); if (active) setGraph(graph); }
        catch (reason) { if (active) setGraphError(errorMessage(reason)); }
      }
    }).catch((reason: unknown) => { if (active) { setReport(null); setError(errorMessage(reason)); } }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; controller.abort(); };
  }, [runId, revision, retry]);
  async function download() {
    setDownloading(true); setDownloadError("");
    try { await api.downloadReport(runId); } catch (reason) { setDownloadError(errorMessage(reason)); }
    finally { setDownloading(false); }
  }
  const unavailable = report?.availability === "missing" ? "报告文件丢失" : report?.availability === "blocked" || task!.status === "failed" ? "研究未通过，报告不可作为成功结果展示" : "报告尚未生成";
  return <div className="stack">
    <div className="section-heading"><h2>研究报告</h2><div className="run-actions"><Button variant="secondary" onClick={() => setRetry((value) => value + 1)}>刷新报告</Button><Button disabled={!report?.exists || loading || !!error} loading={downloading} onClick={download}>下载 Markdown</Button></div></div>
    {loading && <p role="status">正在读取报告与引用关系…</p>}
    {error && <div className="error-banner" role="alert">报告读取失败：{error}</div>}
    {downloadError && <div className="error-banner" role="alert">下载失败：{downloadError}</div>}
    {graphError && <div className="warning-banner">引用图谱读取失败：{graphError}。以下引用暂不可解析，不代表已验证。</div>}
    {report && !report.exists && !loading && <Panel title={unavailable}><p>{report.availability === "missing" ? "数据库记录了报告路径，但文件不存在。请检查部署端 workspace 挂载或备份；不会用诊断文本冒充报告。" : "请返回工作台查看配置、审批、执行状态或失败原因。"}</p><p>{report.message}</p><Link className="source-link" to={`/runs/${runId}`}>返回工作台</Link></Panel>}
    {report?.exists && <>
      <aside className="warning-banner">{report.requires_review ? "此为历史报告，尚未按当前规则复核。" : "报告生成不等于验收通过。"} 点击可解析的引用编号核对原始片段；无有效引用时不可评估。</aside>
      {report.quality_warnings?.map((warning) => <p className="warning-banner" key={warning}>{warning}</p>)}
      <Panel><SafeMarkdown markdown={report.markdown} runId={runId} citations={targets} /></Panel>
      <p className="muted">安全阅读模式：不执行原始 HTML、不加载外部图片。复杂 Markdown 可下载原文件查看。</p>
    </>}
  </div>;
}
