import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ApiError, api, statusLabel, statusTone, type PlanReviewResponse } from "../api/client";
import { Button, PageHeader, Panel, StatusChip, TimelineRow } from "../components/primitives";
import { ErrorState, LoadingState } from "../components/Feedback";
import { ResearchPolicyNote } from "../components/ResearchPolicyNote";

type ApprovalState = "awaiting" | "submitting" | "error" | "approved" | "rejected";

export function PlanReviewPage() {
  const { runId = "" } = useParams();
  return <PlanReview key={runId} runId={runId} />;
}

function PlanReview({ runId }: { runId: string }) {
  const navigate = useNavigate();
  const mounted = useRef(true);
  const lock = useRef(false);
  const checkingLock = useRef(false);
  const [revision, setRevision] = useState(0);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const [plan, setPlan] = useState<PlanReviewResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [approvalState, setApprovalState] = useState<ApprovalState>("awaiting");
  const [decision, setDecision] = useState<boolean | null>(null);
  const [error, setError] = useState("");
  const [checking, setChecking] = useState(false);

  async function refreshReadiness() {
    if (lock.current || checkingLock.current) return;
    checkingLock.current = true;
    setChecking(true);
    try {
      const preflight = await api.preflightTask(runId);
      if (!mounted.current) return;
      setPlan((current) => current ? { ...current, preflight } : current);
      setError("");
      setApprovalState("awaiting");
    } catch (reason) { if (mounted.current) setError(reason instanceof Error ? reason.message : "配置检查失败"); }
    finally { checkingLock.current = false; if (mounted.current) setChecking(false); }
  }

  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    setLoading(true); setPlan(null); setError(""); setApprovalState("awaiting");
    api.reviewPlan(runId, controller.signal).then((result) => { if (active) setPlan(result); }).catch((reason: unknown) => { if (active) setError(reason instanceof Error ? reason.message : "读取计划失败"); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; controller.abort(); };
  }, [runId, revision]);

  const toolCount = useMemo(() => new Set(plan?.steps.map((step) => step.tool_name)).size, [plan]);

  async function decide(approved: boolean) {
    if (lock.current || checkingLock.current || plan?.status !== "waiting_human_plan" || (approved && plan.preflight?.ready !== true)) return;
    lock.current = true;
    setDecision(approved);
    setApprovalState("submitting"); setError("");
    try {
      await api.approvePlan(runId, approved, approved ? "approved from local web UI" : "revision requested from local web UI");
      if (!mounted.current) return;
      setApprovalState(approved ? "approved" : "rejected");
      navigate(`/runs/${runId}`);
    } catch (reason) {
      if (!mounted.current) return;
      if (reason instanceof ApiError && (reason.status === 400 || reason.status === 409)) {
        try {
          const latest = await api.getTask(runId);
          if (!mounted.current) return;
          setPlan((current) => current ? { ...current, status: latest.status } : current);
          setError(`${reason.message}。已同步最新 Run 状态：${latest.status}。`);
        } catch {
          if (!mounted.current) return;
          setPlan((current) => current ? { ...current, status: "unknown" } : current);
          setError(`${reason.message}。计划状态可能已变化。`);
        }
      } else {
        setError(reason instanceof Error ? reason.message : "提交审批失败");
      }
      setApprovalState("error");
    } finally { lock.current = false; }
  }

  if (loading) return <div className="page"><PageHeader title="研究计划复核" subtitle="正在从本地 SQLite 读取计划" /><Panel><LoadingState>加载中…</LoadingState></Panel></div>;
  if (!plan) return <div className="page stack"><PageHeader title="研究计划复核" subtitle="无法读取当前计划" /><ErrorState message={error || "计划不存在"} retry={() => setRevision((value) => value + 1)} /><Button variant="secondary" onClick={() => navigate("/runs")}>返回任务列表</Button></div>;
  const riskSummary = plan.risk_summary ?? { low: 0, medium: 0, high: 0 };
  const notes = plan.notes ?? [];

  return (
    <div className="page" data-figma-screen="32:142">
      <PageHeader title="研究计划复核" subtitle="在任何工具执行前确认步骤、参数与风险" action={<StatusChip tone={statusTone(plan.status)}>{statusLabel(plan.status)}</StatusChip>} />
      <section className="panel plan-summary">
        <div className="plan-summary-main"><h2 className="plan-task-title">{plan.task}</h2><p className="plan-meta">Run {plan.run_id.slice(0, 8)} · Planner: {plan.planner_source || "deterministic"} · {plan.execution_mode}</p></div>
        <div><div className="plan-metric-label">步骤</div><div className="plan-metric-value">{plan.steps.length}</div></div>
        <div><div className="plan-metric-label">工具</div><div className="plan-metric-value">{toolCount}</div></div>
        <div><div className="plan-metric-label">预估 tokens</div><div className="plan-metric-value">{plan.estimated_total_tokens}</div></div>
        <div><div className="plan-metric-label">高风险</div><div className="plan-metric-value">{riskSummary.high ?? 0}</div></div>
      </section>
      <div className="plan-layout">
        <Panel title="计划步骤">
          {plan.steps.length === 0 && <p>没有静态步骤；动态执行的具体动作以工作台 Trace 为准。</p>}
          <div className="timeline-list">{plan.steps.map((step) => <TimelineRow key={step.step_no} index={step.step_no} title={step.goal} meta={`${step.tool_name} · 输出参数将在 Trace 中保留`} risk={step.risk_level} />)}</div>
          <div className="editable-boundary section-gap"><strong>可编辑参数边界</strong><p>后端支持通过 modified_steps 修改参数；当前首版界面仅提交原计划，避免提供后端未验证的步骤删除或启停。</p></div>
        </Panel>
        <Panel title="风险与配置">
          {!plan.preflight && <ErrorState message="尚未取得配置预检结果，不能批准启动。" retry={refreshReadiness} retryLabel="重新检查配置" />}
          {plan.preflight && <div className="section-gap" aria-live="polite">
            <strong>{plan.preflight.ready ? "必要配置已就绪（尚未验证联网）" : "当前配置无法启动此计划"}</strong>
            {plan.preflight.blockers.map((issue, index) => <p key={`${issue.capability}-${index}`} role="alert">{issue.message}</p>)}
            {plan.preflight.warnings.map((warning) => <p key={warning}>{warning}</p>)}
            <p>请在实际部署目录的 .env 配置密钥后重新创建 API 容器，使新环境变量生效；本页面不会读取或保存密钥。</p>
            <Button variant="secondary" loading={checking} disabled={approvalState === "submitting"} onClick={refreshReadiness}>重新检查配置</Button>
          </div>}
          <div className="risk-list">
            <div><div className="risk-label">低风险</div><div className="risk-value">{riskSummary.low ?? 0} 步</div></div>
            <div><div className="risk-label">中风险</div><div className="risk-value">{riskSummary.medium ?? 0} 步</div></div>
            <div><div className="risk-label">高风险</div><div className="risk-value">{riskSummary.high ?? 0} 步 · 运行时仍需确认</div></div>
            <div><div className="risk-label">允许工具</div><div className="risk-value">{plan.allowed_tools.join("、") || "空名单：不允许执行任何工具"}</div></div>
            <div><div className="risk-label">计划备注</div><div className="risk-value">{notes.join("；") || "无额外备注"}</div></div>
          </div>
        </Panel>
      </div>
      <ResearchPolicyNote sourceMode={plan.source_mode} executionMode={plan.execution_mode} />
      <section className="panel approval-bar" data-figma-node="40:29" aria-live="polite">
        <div className={`approval-message${error ? " approval-error" : ""}`}>{error || (plan.status !== "waiting_human_plan" ? `当前状态：${statusLabel(plan.status)}，此页面只读。` : approvalState === "approved" ? "计划已批准，正在进入工作台" : approvalState === "rejected" ? "计划已拒绝，Run 将保留审计记录" : "研究计划等待确认 · 批准后异步启动并进入工作台")}</div>
        {approvalState === "error" && <Button variant="secondary" onClick={() => setRevision((value) => value + 1)}>重新读取计划</Button>}
        {approvalState === "error" || plan.status !== "waiting_human_plan" ? <Button variant="secondary" onClick={() => navigate(`/runs/${runId}`)}>打开工作台</Button> : <Button variant="secondary" loading={approvalState === "submitting" && decision === false} disabled={checking || approvalState === "submitting" || approvalState === "approved" || approvalState === "rejected"} onClick={() => decide(false)}>{approvalState === "submitting" && decision === false ? "正在拒绝" : "拒绝计划"}</Button>}
        <Button loading={approvalState === "submitting" && decision === true} disabled={plan.status !== "waiting_human_plan" || checking || plan.preflight?.ready !== true || approvalState === "submitting" || approvalState === "approved" || approvalState === "rejected" || approvalState === "error"} onClick={() => decide(true)}>{approvalState === "submitting" && decision === true ? "正在批准" : "批准并启动"}</Button>
      </section>
    </div>
  );
}
