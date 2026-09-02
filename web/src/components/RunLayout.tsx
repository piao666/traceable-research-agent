import { useEffect, useRef, useState } from "react";
import { Link, NavLink, Outlet, useNavigate, useParams } from "react-router-dom";
import { api, errorMessage, formatTimestamp, taskStatusLabel, taskStatusTone } from "../api/client";
import { useRun } from "../hooks/useRun";
import type { RunContext } from "../hooks/useRunContext";
import { Button, PageHeader, Panel, StatusChip } from "./primitives";
import { Modal } from "./Modal";
import { LoadingState } from "./Feedback";

type Action = "cancel" | "retry" | "approve" | "reject" | "start";
const actionLabels: Record<Action, string> = { cancel: "取消任务", retry: "完整重试", approve: "批准并继续", reject: "拒绝执行", start: "启动研究" };
const connectionLabels = { loading: "正在读取", connecting: "连接实时更新", live: "实时连接正常", polling: "轮询恢复中 · 每 5 秒同步", paused: "等待操作 · 每 5 秒同步", closed: "任务已结束 · 实时连接已关闭" };

export function IntegrityNotice({ task }: { task: NonNullable<RunContext["task"]> }) {
  const warnings = [...new Set(task.quality_warnings ?? [])];
  if (!task.requires_review && !warnings.length && task.status !== "failed") return null;
  return <aside className="warning-banner" aria-label="研究限制">
    {task.requires_review && <strong>历史结果待复核：旧状态和旧质量分数不能证明研究有效。</strong>}
    {task.status === "failed" && <strong>研究失败，不能作为成功结果验收。</strong>}
    {warnings.length > 0 && <ul>{warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>}
  </aside>;
}

export function RunLayout() {
  const { runId = "" } = useParams();
  // Key the inner view to prevent old task data/actions flashing on route changes.
  return <RunView key={runId} runId={runId} />;
}

function RunView({ runId }: { runId: string }) {
  const context = useRun(runId);
  const { task, loading, error, detailErrors, connection, refresh } = context;
  const navigate = useNavigate();
  const [action, setAction] = useState<Action | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState("");
  const [comment, setComment] = useState("");
  const [message, setMessage] = useState("");
  const lock = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);

  async function perform() {
    if (!action || lock.current) return;
    lock.current = true; setBusy(true); setActionError("");
    try {
      if (action === "retry") {
        const created = await api.retryTask(runId);
        if (!mounted.current) return;
        navigate(`/runs/${created.run_id}${created.status === "waiting_human_plan" ? "/plan" : ""}`);
      } else {
        if (action === "cancel") await api.cancelTask(runId, comment || "Cancelled from local web UI");
        if (action === "approve" || action === "reject") await api.confirmTask(runId, action === "approve", comment);
        if (action === "start") await api.startTask(runId);
        if (!mounted.current) return;
        setMessage("操作已提交，正在同步后端状态。"); refresh();
      }
      setAction(null);
    } catch (reason) {
      if (mounted.current) { setActionError(`${errorMessage(reason)}。未自动重试，请先核对最新状态。`); refresh(); }
    } finally { lock.current = false; if (mounted.current) setBusy(false); }
  }
  function choose(next: Action) { setComment(""); setActionError(""); setAction(next); }

  if (loading && !task) return <div className="page"><PageHeader title="研究详情" subtitle={`Run ${runId}`} /><Panel><LoadingState>正在读取研究状态、计划和 Trace…</LoadingState></Panel></div>;
  if (!task) return <div className="page stack"><PageHeader title="无法读取研究" subtitle={`Run ${runId}`} /><div role="alert" className="error-banner">{error || "任务不存在"}</div><Button variant="secondary" onClick={refresh}>重新加载</Button><Link to="/runs">返回研究任务</Link></div>;
  const mayCancel = ["pending", "running", "waiting_human", "waiting_human_plan"].includes(task.status);
  const mayRetry = ["failed", "cancelled"].includes(task.status);
  return <div className="page run-page stack">
    <PageHeader title="研究详情" subtitle={`Run ${runId}`} action={<StatusChip tone={taskStatusTone(task)}>{taskStatusLabel(task)}</StatusChip>} />
    <section className="panel run-heading">
      <h2>{task.task}</h2>
      <div className="run-meta"><span>{task.execution_mode} · {task.source_mode}</span><span>更新于 {formatTimestamp(task.updated_at)}</span><span role="status">{connectionLabels[connection]}</span></div>
      <div className="run-actions">
        <Button variant="secondary" onClick={refresh}>刷新状态</Button>
        {task.status === "waiting_human_plan" && <Link className="button button-primary" to={`/runs/${runId}/plan`}>审阅计划</Link>}
        {task.status === "pending" && <Button disabled={busy || !!error || !context.plan} onClick={() => choose("start")}>启动研究</Button>}
        {task.status === "waiting_human" && <>
          <Button disabled={busy || !!error || !context.plan} onClick={() => choose("approve")}>批准并继续</Button>
          <Button variant="danger" disabled={busy || !!error} onClick={() => choose("reject")}>拒绝执行</Button>
        </>}
        {mayCancel && <Button variant="danger" disabled={busy || !!error} onClick={() => choose("cancel")}>取消任务</Button>}
        {mayRetry && <Button disabled={busy || !!error} onClick={() => choose("retry")}>完整重试</Button>}
      </div>
    </section>
    {error && <div className="error-banner" role="alert">状态同步失败，当前显示上次快照：{error}</div>}
    {detailErrors.map((value) => <div key={value} className="error-banner" role="alert">{value}。可使用“刷新状态”重试。</div>)}
    {message && <p role="status">{message}</p>}
    <IntegrityNotice task={task} />
    {task.error_message && <div className={task.status === "failed" ? "error-banner" : "warning-banner"}><strong>运行说明：</strong>{task.error_message}</div>}
    <nav className="run-tabs" aria-label="研究详情导航">
      <NavLink end to={`/runs/${runId}`}>实时工作台</NavLink>
      <NavLink to={`/runs/${runId}/evidence`}>证据追踪</NavLink>
      <NavLink to={`/runs/${runId}/report`}>研究报告</NavLink>
    </nav>
    <Outlet context={context} />
    {action && <Modal title={`确认${actionLabels[action]}`} busy={busy} close={() => setAction(null)} description={action === "retry" ? "创建新的 Run，从头执行原计划并读取当前配置；原任务和 Trace 保留，不继承旧审批，也不会立即调用工具。" : action === "cancel" ? "停止后续步骤。已发出的外部请求可能仍会结束，但任务不得再次变为已完成；历史记录会保留。" : action === "reject" ? "拒绝本次操作后任务将失败，Trace 会保留。" : "此操作将启动真实执行，可能产生 API 费用。请先核对计划、工具参数和确认范围。"}>
      {action === "approve" && context.plan && <pre className="json-block" tabIndex={0}>{JSON.stringify(context.plan.react_state?.pending_confirmation ?? context.plan.steps.find((step) => step.step_no > task.current_step && step.requires_confirmation) ?? context.plan.confirmation, null, 2)}</pre>}
      {(action === "cancel" || action === "approve" || action === "reject") && <label className="field">操作备注（可选）<textarea className="textarea" value={comment} onChange={(event) => setComment(event.target.value)} maxLength={1000} /></label>}
      {actionError && <p className="error-banner" role="alert">{actionError}</p>}
      <div className="run-actions"><Button variant="secondary" disabled={busy} onClick={() => setAction(null)}>返回</Button><Button variant={action === "cancel" || action === "reject" ? "danger" : "primary"} loading={busy} onClick={perform}>确认{actionLabels[action]}</Button></div>
    </Modal>}
  </div>;
}
