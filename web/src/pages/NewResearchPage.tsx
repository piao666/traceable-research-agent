import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { Button, OptionCard, PageHeader, Panel, StatusChip } from "../components/primitives";

type TemplateKey = "standard" | "deep_web_research" | "local_audit";
type ExecutionMode = "planned" | "react";

const templateOptions: Array<{ key: TemplateKey; title: string; description: string }> = [
  { key: "standard", title: "快速搜索", description: "少量来源，快速形成摘要" },
  { key: "deep_web_research", title: "深度 Web", description: "多来源检索与交叉验证" },
  { key: "local_audit", title: "本地审计", description: "优先读取 workspace/docs" },
];

export function NewResearchPage() {
  const navigate = useNavigate();
  const [task, setTask] = useState(() => sessionStorage.getItem("tra:new-task") ?? "");
  const [template, setTemplate] = useState<TemplateKey>("deep_web_research");
  const [mode, setMode] = useState<ExecutionMode>("planned");
  const [reportType, setReportType] = useState("detailed_report");
  const [retrievalProfile, setRetrievalProfile] = useState("generic");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => { sessionStorage.setItem("tra:new-task", task); }, [task]);

  async function submit() {
    const value = task.trim();
    if (!value) { setError("请输入研究问题或目标。"); return; }
    setSubmitting(true); setError("");
    try {
      const result = await api.createTask({
        task: value,
        report_type: reportType,
        source_mode: template === "local_audit" ? "mock" : "real",
        execution_mode_override: mode,
        scenario_template_key: template,
        skill_name: template === "local_audit" ? "local_audit" : undefined,
        require_plan_approval: true,
        retrieval_profile: retrievalProfile,
      });
      sessionStorage.removeItem("tra:new-task");
      navigate(`/runs/${result.run_id}/plan`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "创建研究任务失败");
    } finally { setSubmitting(false); }
  }

  return (
    <div className="page" data-figma-screen="32:45">
      <PageHeader title="新建研究" subtitle="定义问题、选择执行策略，并在工具运行前审阅计划" action={<span className="draft-chip">草稿自动保留</span>} />
      <div className="form-layout">
        <div className="stack">
          <Panel className="form-section">
            <label className="field" htmlFor="research-task"><span className="field-label">研究问题或目标</span><textarea id="research-task" className="textarea" value={task} onChange={(event) => setTask(event.target.value)} placeholder="例如：比较主流 Agent 评测框架，并给出适合本项目的可追溯评测方案。" /><span className="field-help">支持多行输入；草稿仅保存在当前浏览器会话</span></label>
          </Panel>
          <Panel className="form-section">
            <h2 className="form-section-title">场景模板</h2>
            <div className="options-grid">{templateOptions.map((option) => <OptionCard key={option.key} title={option.title} description={option.description} selected={template === option.key} onClick={() => setTemplate(option.key)} />)}</div>
          </Panel>
          <Panel className="form-section">
            <h2 className="form-section-title">执行模式</h2>
            <div className="options-grid">
              <OptionCard title="Planned" description="先生成并审阅计划" selected={mode === "planned"} onClick={() => setMode("planned")} />
              <OptionCard title="ReAct" description="执行中动态决策" selected={mode === "react"} onClick={() => setMode("react")} />
            </div>
            <p className="field-help">✓ 执行前需要计划审批（推荐）</p>
          </Panel>
          <Panel className="form-section">
            <h2 className="form-section-title">高级设置</h2>
            <div className="advanced-grid">
              <label className="field"><span className="field-label">报告类型</span><select className="input" value={reportType} onChange={(event) => setReportType(event.target.value)}><option value="summary">研究摘要</option><option value="detailed_report">详细研究报告</option></select><span className="field-help">报告写入本地 workspace</span></label>
              <label className="field"><span className="field-label">检索策略</span><select className="input" value={retrievalProfile} onChange={(event) => setRetrievalProfile(event.target.value)}><option value="generic">均衡</option><option value="academic_literature">学术优先</option><option value="technical_facts">技术事实优先</option><option value="public_opinion">公众观点</option></select><span className="field-help">使用后端 source_policy.v2 已支持的检索档案</span></label>
            </div>
          </Panel>
        </div>
        <Panel title="运行摘要" className="run-summary">
          <div className="stack">
            <StatusChip tone="success">本地 workspace</StatusChip>
            <p>创建后先进入计划审阅；任何工具都不会在批准前执行。</p>
            <div className="summary-row"><span>模板</span><strong>{templateOptions.find((item) => item.key === template)?.title}</strong></div>
            <div className="summary-row"><span>模式</span><strong>{mode === "planned" ? "Planned" : "ReAct"}</strong></div>
            <div className="summary-row"><span>报告</span><strong>{reportType === "summary" ? "研究摘要" : "详细报告"}</strong></div>
            <div className="summary-row"><span>审批</span><strong>必需</strong></div>
            <div className="summary-callout">本界面不收集账号或密钥。任务、计划和报告通过本地 FastAPI 保存在 SQLite 与 workspace。</div>
            {error && <div className="error-banner" role="alert">{error}</div>}
            <Button loading={submitting} onClick={submit}>{submitting ? "正在生成计划" : "创建并审阅计划"}</Button>
          </div>
        </Panel>
      </div>
    </div>
  );
}
