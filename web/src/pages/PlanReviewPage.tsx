import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ApiError, api, type PlanReviewResponse } from "../api/client";
import { Button, PageHeader, Panel, StatusChip, TimelineRow } from "../components/primitives";

type ApprovalState = "awaiting" | "submitting" | "error" | "approved" | "rejected";

export function PlanReviewPage() {
  const { runId = "" } = useParams();
  const navigate = useNavigate();
  const [plan, setPlan] = useState<PlanReviewResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [approvalState, setApprovalState] = useState<ApprovalState>("awaiting");
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    setLoading(true);
    api.reviewPlan(runId).then((result) => { if (active) setPlan(result); }).catch((reason: unknown) => { if (active) setError(reason instanceof Error ? reason.message : "读取计划失败"); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [runId]);

  const toolCount = useMemo(() => new Set(plan?.steps.map((step) => step.tool_name)).size, [plan]);

  async function decide(approved: boolean) {
    if (approvalState === "submitting") return;
    setApprovalState("submitting"); setError("");
    try {
      await api.approvePlan(runId, approved, approved ? "approved from local web UI" : "revision requested from local web UI");
      setApprovalState(approved ? "approved" : "rejected");
      window.setTimeout(() => navigate(approved ? "/runs?status=running" : "/runs?status=failed"), 500);
    } catch (reason) {
      if (reason instanceof ApiError && (reason.status === 400 || reason.status === 409)) {
        try {
          const latest = await api.getTask(runId);
          setError(`${reason.message}。已同步最新 Run 状态：${latest.status}。`);
        } catch {
          setError(`${reason.message}。计划状态可能已变化。`);
        }
      } else {
        setError(reason instanceof Error ? reason.message : "提交审批失败");
      }
      setApprovalState("error");
    }
  }

  if (loading) return <div className="page"><PageHeader title="研究计划复核" subtitle="正在从本地 SQLite 读取计划" /><Panel>加载中…</Panel></div>;
  if (!plan) return <div className="page"><PageHeader title="研究计划复核" subtitle="无法读取当前计划" /><div className="error-banner">{error || "计划不存在"}</div><Button variant="secondary" onClick={() => navigate("/runs")}>返回任务列表</Button></div>;
  const riskSummary = plan.risk_summary ?? { low: 0, medium: 0, high: 0 };
  const notes = plan.notes ?? [];

  return (
    <div className="page" data-figma-screen="32:142">
      <PageHeader title="研究计划复核" subtitle="在任何工具执行前确认步骤、参数与风险" action={<StatusChip tone="plan">等待计划审批</StatusChip>} />
      <section className="panel plan-summary">
        <div className="plan-summary-main"><h2 className="plan-task-title">{plan.task}</h2><p className="plan-meta">Run {plan.run_id.slice(0, 8)} · Planner: {plan.planner_source || "deterministic"} · {plan.execution_mode}</p></div>
        <div><div className="plan-metric-label">步骤</div><div className="plan-metric-value">{plan.steps.length}</div></div>
        <div><div className="plan-metric-label">工具</div><div className="plan-metric-value">{toolCount}</div></div>
        <div><div className="plan-metric-label">预估 tokens</div><div className="plan-metric-value">{plan.estimated_total_tokens}</div></div>
        <div><div className="plan-metric-label">高风险</div><div className="plan-metric-value">{riskSummary.high ?? 0}</div></div>
      </section>
      <div className="plan-layout">
        <Panel title="计划步骤">
          <div className="timeline-list">{plan.steps.map((step) => <TimelineRow key={step.step_no} index={step.step_no} title={step.goal} meta={`${step.tool_name} · 输出参数将在 Trace 中保留`} risk={step.risk_level} />)}</div>
          <div className="editable-boundary section-gap"><strong>可编辑参数边界</strong><p>后端支持通过 modified_steps 修改参数；当前首版界面仅提交原计划，避免提供后端未验证的步骤删除或启停。</p></div>
        </Panel>
        <Panel title="风险与配置">
          <div className="risk-list">
            <div><div className="risk-label">低风险</div><div className="risk-value">{riskSummary.low ?? 0} 步</div></div>
            <div><div className="risk-label">中风险</div><div className="risk-value">{riskSummary.medium ?? 0} 步</div></div>
            <div><div className="risk-label">高风险</div><div className="risk-value">{riskSummary.high ?? 0} 步 · 运行时仍需确认</div></div>
            <div><div className="risk-label">允许工具</div><div className="risk-value">{plan.allowed_tools.join("、") || "由本地注册表决定"}</div></div>
            <div><div className="risk-label">计划备注</div><div className="risk-value">{notes.join("；") || "无额外备注"}</div></div>
          </div>
        </Panel>
      </div>
      <section className="panel approval-bar" data-figma-node="40:29" aria-live="polite">
        <div className={`approval-message${error ? " approval-error" : ""}`}>{error || (approvalState === "approved" ? "计划已批准，正在返回任务列表" : approvalState === "rejected" ? "计划已拒绝，Run 将保留审计记录" : "研究计划等待确认 · 批准后异步启动并进入运行状态")}</div>
        {approvalState === "error" ? <Button variant="secondary" onClick={() => navigate("/runs")}>返回任务列表</Button> : <Button variant="secondary" disabled={approvalState === "submitting" || approvalState === "approved" || approvalState === "rejected"} onClick={() => decide(false)}>拒绝计划</Button>}
        <Button loading={approvalState === "submitting"} disabled={approvalState === "approved" || approvalState === "rejected" || approvalState === "error"} onClick={() => decide(true)}>{approvalState === "submitting" ? "正在批准" : "批准并启动"}</Button>
      </section>
    </div>
  );
}
