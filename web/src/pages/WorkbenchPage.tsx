import { Link, useSearchParams } from "react-router-dom";
import { formatTimestamp, statusLabel, statusTone } from "../api/client";
import { useRunContext } from "../hooks/useRunContext";
import { MetricCard, Panel, StatusChip } from "../components/primitives";
import { useFocusTarget } from "../hooks/useFocusTarget";

export function WorkbenchPage() {
  const { task, plan, traces, detailErrors } = useRunContext();
  const [params] = useSearchParams();
  const traceId = params.get("trace");
  useFocusTarget(traceId ? `trace-${traceId}` : null, traces.some((trace) => trace.trace_id === traceId));
  if (!task) return null;
  const effective = task.research_outcome?.effective_evidence_count;
  return <div className="stack">
    <section className="metrics-grid" aria-label="运行指标">
      <MetricCard label="计划进度" value={`${task.current_step} / ${task.total_steps}`} note="步骤进度不代表研究已通过验收" />
      <MetricCard label="工具调用" value={task.total_tool_calls} note="调用次数不等于有效来源数" />
      <MetricCard label="累计调用耗时" value={`${(task.total_latency_ms / 1000).toFixed(2)} s`} note="累计调用时长，并非总墙钟耗时" />
      <MetricCard label="记录的估算费用" value={task.estimated_cost > 0 ? `$${task.estimated_cost.toFixed(4)}` : "未记录 / 0"} note="非账单金额；零值不保证没有费用" />
    </section>
    <div className="workbench-columns">
      <Panel title="执行计划">
        {!plan ? <p>计划暂不可读，请刷新重试。</p> : <>
          <p>规划器：{plan.planner_source || "未记录"} · 模型：{plan.llm_model || "未记录"}</p>
          {plan.execution_mode === "react" && <p>ReAct 会动态选择工具；以下为已保存计划，实际调用以 Trace 为准。</p>}
          <ol className="plan-steps">{plan.steps.map((step) => <li key={step.step_no}>
            <details><summary><span>{step.step_no}. {step.goal}</span><span className="muted">{step.tool_name}</span></summary><pre className="json-block" tabIndex={0}>{JSON.stringify(step.arguments, null, 2)}</pre><p>完成条件：{step.completion_criteria || "未记录"}</p><p>风险：{step.risk_level}；{step.requires_confirmation ? "需人工确认" : "无需人工确认"}</p></details>
          </li>)}</ol>
          {!plan.steps.length && <p>没有静态步骤，查看下方实际执行 Trace。</p>}
          {plan.notes.map((note, index) => <p key={index}>{note}</p>)}
          {(task.deepening_phase || task.adaptive_phase) && <p>深化阶段：{task.deepening_phase || "无"}；自适应阶段：{task.adaptive_phase || "无"}</p>}
          {!!plan.deepening_sub_run_ids?.length && <><h3>深化子任务</h3><ul>{plan.deepening_sub_run_ids.map((id) => <li key={id}><Link className="source-link" to={`/runs/${encodeURIComponent(id)}`}>{id}</Link></li>)}</ul><p>子任务学习笔记不自动等于主报告的受支持结论。</p></>}
        </>}
      </Panel>
      <Panel title="结果检查">
        <p>有效证据条目：{typeof effective === "number" ? effective : "尚未评估"}</p>
        <p>引用校验：{task.citation_evaluated && !task.requires_review ? `${task.citation_supported} / ${task.citation_total} 条被评为支持` : "不可评估"}</p>
        <p>引用校验不等于事实准确率；请逐条核对来源正文和结论。</p>
        <p>不能仅凭 completed 状态或生成 Markdown 认定研究通过。</p>
        {task.status === "waiting_human" && <p className="warning-banner">运行已暂停，请核对待确认操作，再选择批准或拒绝。</p>}
      </Panel>
    </div>
    <Panel title="持久化 Trace">
      <p>每次调用与内部审计分开留痕。错误、审批和空结果不是研究来源。</p>
      {!traces.length && <p className="empty-state">{detailErrors.some((error) => error.startsWith("Trace")) ? "Trace 读取失败，不能据此判断没有调用。" : "尚无执行记录。任务可能仍在等待配置、审批或启动。"}</p>}
      <div className="trace-list">{traces.map((trace) => <details className={`trace-item${trace.trace_id === traceId ? " selected-evidence" : ""}`} key={trace.trace_id} id={`trace-${trace.trace_id}`} tabIndex={-1} open={trace.trace_id === traceId || undefined}>
        <summary><span><strong>{trace.tool_name}</strong> · 步骤 {trace.step_no}<small>{formatTimestamp(trace.created_at)} · {trace.latency_ms == null ? "耗时未记录" : `${trace.latency_ms} ms`}</small></span><StatusChip tone={statusTone(trace.status === "success" ? "completed" : trace.status)}>{({ success: "调用成功", skipped: "未执行", approved: "已批准", rejected: "已拒绝" } as Record<string, string>)[trace.status] || statusLabel(trace.status)}</StatusChip></summary>
        {trace.error_message && <p className="error-banner">{trace.error_message}</p>}
        <p>{trace.output_summary || "无输出摘要"}</p><p>Trace ID：{trace.trace_id}</p>
        <p>Token：输入 {trace.token_in ?? 0} / 输出 {trace.token_out ?? 0}；估算费用：${(trace.estimated_cost ?? 0).toFixed(4)}</p>
        <h3>输入摘要</h3><pre className="json-block" tabIndex={0}>{trace.input_summary || "未记录"}</pre>
        <h3>持久化输出</h3><pre className="json-block" tabIndex={0}>{trace.output == null ? "未记录" : JSON.stringify(trace.output, null, 2)}</pre>
        {trace.metadata && <><h3>执行元数据</h3><pre className="json-block" tabIndex={0}>{JSON.stringify(trace.metadata, null, 2)}</pre></>}
      </details>)}</div>
      {traceId && traces.length > 0 && !traces.some((trace) => trace.trace_id === traceId) && <p role="alert">找不到指定 Trace，请核对来源关联。</p>}
    </Panel>
  </div>;
}
