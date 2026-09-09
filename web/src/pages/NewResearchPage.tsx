import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, type RuntimePreflightResponse } from "../api/client";
import { Button, OptionCard, PageHeader, Panel, StatusChip } from "../components/primitives";
import { useResource } from "../hooks/useResource";
import { ResourceState } from "../components/ResourceState";
import { readDraft, saveDraft, removeDraft } from "../lib/draft";

type TemplateKey = "standard" | "deep_web_research" | "local_audit";

const templateOptions: Array<{ key: TemplateKey; title: string; description: string }> = [
  { key: "standard", title: "快速搜索", description: "少量来源，快速形成摘要" },
  { key: "deep_web_research", title: "深度 Web", description: "多来源检索与交叉验证" },
  { key: "local_audit", title: "本地审计", description: "优先读取 workspace/docs" },
];

export function NewResearchPage() {
  const [params] = useSearchParams();
  const sessionId = params.get("session_id") || "";
  return <NewResearchForm key={sessionId} sessionId={sessionId} />;
}

function NewResearchForm({ sessionId }: { sessionId: string }) {
  const navigate = useNavigate();
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const draftKey = sessionId ? `tra:new-task:${sessionId}` : "tra:new-task";
  const session = useResource(useCallback((signal: AbortSignal) => sessionId ? api.session(sessionId, signal) : Promise.resolve(null), [sessionId]));
  const runtime = useResource(api.capabilities);
  const [task, setTask] = useState(() => readDraft(draftKey));
  const [draftSaved, setDraftSaved] = useState(true);
  const [invalid, setInvalid] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const [template, setTemplate] = useState<TemplateKey>("deep_web_research");
  const [reportType, setReportType] = useState("detailed_report");
  const [retrievalProfile, setRetrievalProfile] = useState("auto");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [preflight, setPreflight] = useState<RuntimePreflightResponse | null>(null);
  const [checkingRuntime, setCheckingRuntime] = useState(false);
  const checkedCapability = (name: string) => preflight?.capabilities.find((item) => item.name === name);
  const verifiedStatus = (name: string, configured: boolean) => {
    const checked = checkedCapability(name);
    if (checked) return checked.usable ? "已连接" : "验证失败";
    return configured ? "已配置（未验证）" : "未配置";
  };
  useEffect(() => { setDraftSaved(saveDraft(draftKey, task)); }, [task, draftKey]);

  async function submit() {
    if (submitting || (sessionId && !session.data)) return;
    const value = task.trim();
    if (!value) { setInvalid(true); setError("请输入研究问题或目标。"); inputRef.current?.focus(); return; }
    setSubmitting(true); setError("");
    try {
      const result = await api.createTask({
        task: value,
        report_type: reportType,
        source_mode: template === "local_audit" ? "mock" : "real",
        scenario_template_key: template,
        skill_name: template === "local_audit" ? "local_audit" : undefined,
        require_plan_approval: true,
        ...(retrievalProfile === "auto" ? {} : { retrieval_profile: retrievalProfile }),
        session_id: sessionId || undefined,
      });
      removeDraft(draftKey);
      if (mounted.current) navigate(`/runs/${result.run_id}/plan`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "创建研究任务失败");
    } finally { setSubmitting(false); }
  }

  async function verifyRuntime() {
    if (checkingRuntime) return;
    setCheckingRuntime(true); setError("");
    try {
      const result = await api.runtimePreflight();
      if (mounted.current) setPreflight(result);
    } catch (reason) {
      if (mounted.current) setError(reason instanceof Error ? reason.message : "研究环境验证失败");
    } finally {
      if (mounted.current) setCheckingRuntime(false);
    }
  }

  return (
    <div className="page" data-figma-screen="32:45">
      <PageHeader title="新建研究" subtitle="定义问题，系统将动态选择执行方式，并在工具运行前审阅计划" action={<span className="draft-chip">{draftSaved ? "草稿自动保留" : "草稿未保存"}</span>} />
      {!draftSaved && <p className="warning-banner" role="status">浏览器存储不可用，草稿无法自动保留；离开页面前请自行复制研究问题。</p>}
      {sessionId && <Panel title="关联会话"><ResourceState resource={session} />{session.data && <p>本次研究属于 <Link to={`/sessions/${encodeURIComponent(sessionId)}`}>{session.data.title || "未命名会话"}</Link>。请在问题中写明后续研究所需背景；会话关联不代表自动注入全部历史正文。</p>}<Link to="/research/new">改为独立研究（保留本会话草稿）</Link></Panel>}
      <div className="form-layout">
        <div className="stack">
          <Panel className="form-section">
            <label className="field" htmlFor="research-task"><span className="field-label">研究问题或目标</span><textarea id="research-task" ref={inputRef} aria-invalid={invalid} aria-describedby={invalid ? "research-error research-help" : "research-help"} className="textarea" value={task} onChange={(event) => { setTask(event.target.value); setInvalid(false); }} placeholder="例如：比较主流 Agent 评测框架，并给出适合本项目的可追溯评测方案。" /><span id="research-help" className="field-help">支持多行输入；草稿仅保存在当前浏览器会话</span></label>
          </Panel>
          <Panel className="form-section">
            <h2 className="form-section-title">场景模板</h2>
            <div className="options-grid">{templateOptions.map((option) => <OptionCard key={option.key} title={option.title} description={option.description} selected={template === option.key} onClick={() => setTemplate(option.key)} />)}</div>
          </Panel>
          <Panel className="form-section">
            <h2 className="form-section-title">高级设置</h2>
            <div className="advanced-grid">
              <label className="field"><span className="field-label">报告类型</span><select className="input" value={reportType} onChange={(event) => setReportType(event.target.value)}><option value="summary">研究摘要</option><option value="detailed_report">详细研究报告</option></select></label>
              <label className="field"><span className="field-label">检索策略</span><select className="input" value={retrievalProfile} onChange={(event) => setRetrievalProfile(event.target.value)}><option value="auto">自动（推荐）</option><option value="generic">均衡</option><option value="academic_literature">学术优先</option><option value="technical_facts">技术事实优先</option><option value="public_opinion">公众观点</option></select></label>
            </div>
          </Panel>
        </div>
        <Panel title="运行摘要" className="run-summary">
          <div className="stack">
            <ResourceState resource={runtime} />
            {runtime.data && <>
              <StatusChip tone={runtime.data.research_environment_ready ? "success" : "warning"}>研究运行环境：{runtime.data.research_environment_ready ? "配置就绪" : "需要配置"}</StatusChip>
              <div className="summary-row"><span>运行档位</span><strong>{{ deep: "深度研究", standard: "标准研究", offline: "离线开发" }[runtime.data.research_profile ?? "standard"]}</strong></div>
              <div className="summary-row"><span>大模型</span><strong>{runtime.data.offline_mode ? "离线模式" : verifiedStatus("llm", runtime.data.llm_configured)}</strong></div>
              <div className="summary-row"><span>外部搜索</span><strong>{runtime.data.offline_mode ? "离线数据" : verifiedStatus(runtime.data.search_provider, runtime.data.tavily_configured)}</strong></div>
              <div className="summary-row"><span>网页 / PDF</span><strong>{preflight ? verifiedStatus("web_fetcher", true) : runtime.data.items?.find((item) => item.name === "web_fetcher")?.usable ? "本地能力可用" : "不可用"} / {runtime.data.items?.find((item) => item.name === "pdf_reader")?.usable ? "可用" : "不可用"}</strong></div>
            </>}
            {preflight && <div className={preflight.ready ? "success-banner" : "error-banner"} role="status">真实连接验证：{preflight.ready ? "通过" : "未通过"}{preflight.blockers.length ? `；${preflight.blockers.map((item) => item.message).join("；")}` : ""}</div>}
            {runtime.data && !runtime.data.offline_mode && <Button variant="secondary" loading={checkingRuntime} onClick={verifyRuntime}>{checkingRuntime ? "正在验证真实连接" : "验证真实连接"}</Button>}
            <p>创建后先进入计划审阅；任何工具都不会在批准前执行。</p>
            <div className="summary-row"><span>模板</span><strong>{templateOptions.find((item) => item.key === template)?.title}</strong></div>
            <div className="summary-row"><span>执行方式</span><strong>系统动态选择</strong></div>
            <div className="summary-row"><span>报告</span><strong>{reportType === "summary" ? "研究摘要" : "详细报告"}</strong></div>
            <div className="summary-row"><span>审批</span><strong>必需</strong></div>
            {error && <div id="research-error" className="error-banner" role="alert">{error}</div>}
            <Button loading={submitting} disabled={!!sessionId && !session.data} onClick={submit}>{submitting ? "正在生成计划" : "创建并审阅计划"}</Button>
          </div>
        </Panel>
      </div>
    </div>
  );
}
