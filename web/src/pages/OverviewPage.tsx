import { useCallback, useMemo } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, formatTimestamp, taskStatusLabel, taskStatusTone } from "../api/client";
import { Button, MetricCard, PageHeader, Panel, StatusChip, TableRow } from "../components/primitives";
import { ResourceState } from "../components/ResourceState";
import { EmptyState } from "../components/Feedback";
import { useResource } from "../hooks/useResource";

export function OverviewPage() {
  const navigate = useNavigate();
  const list = useResource(useCallback((signal: AbortSignal) => api.listTasks(50, 0, {}, signal), []));
  const health = useResource(api.health);
  const tasks = list.data?.tasks;
  const counts = useMemo(() => tasks ? {
    running: tasks.filter((task) => task.status === "running").length,
    waiting: tasks.filter((task) => task.status === "waiting_human" || task.status === "waiting_human_plan").length,
    completed: tasks.filter((task) => task.status === "completed" && !task.requires_review).length,
    failed: tasks.filter((task) => task.status === "failed").length,
  } : null, [tasks]);
  return <div className="page" data-figma-screen="32:2">
    <PageHeader title="概览" subtitle="快速掌握本地实例、研究任务与待处理事项" action={<div className="r5-actions"><Button variant="secondary" onClick={() => { list.refresh(); health.refresh(); }}>刷新</Button><Button onClick={() => navigate("/research/new")}>新建研究</Button></div>} />
    <ResourceState resource={list} />
    <section className="metrics-grid" aria-label="最近 50 条研究任务指标" aria-busy={list.loading}>
      <MetricCard label="运行中" value={counts?.running ?? "—"} note="最近 50 条任务中的运行数" />
      <MetricCard label="等待人工" value={counts?.waiting ?? "—"} note="最近 50 条中的计划审批与确认" />
      <MetricCard label="已通过检查" value={counts?.completed ?? "—"} note="最近 50 条；不含待复核历史结果" />
      <MetricCard label="失败" value={counts?.failed ?? "—"} note="最近 50 条；可从任务列表重试" />
    </section>
    <section className="two-column">
      <Panel title="需要处理">{counts ? counts.waiting ? <Link className="source-link" to="/runs?status=waiting">最近 50 条中 {counts.waiting} 个任务等待人工确认</Link> : "最近 50 条任务中暂无待处理操作" : "任务数据尚不可用，不能判断是否有待处理操作。"}</Panel>
      <Panel title="本地环境"><ResourceState resource={health} />{health.data && <p>API {health.data.status} · {health.data.service} · {health.data.execution_mode}</p>}<Link className="source-link" to="/system">查看数据库、workspace 与配置诊断</Link></Panel>
    </section>
    <section className="panel table-card" aria-labelledby="recent-title">
      <h2 id="recent-title" className="table-card-title">最近研究任务</h2>
      {tasks && tasks.length > 0 && <div className="table-scroll" role="table" aria-label="最近研究任务">
        <div className="table-header" role="row">{["任务", "状态", "模式 / 工具", "更新时间 / 操作"].map((label) => <div role="columnheader" key={label}>{label}</div>)}</div>
        {tasks.slice(0, 4).map((task) => <TableRow key={task.run_id} primary={task.task} status={<StatusChip tone={taskStatusTone(task)}>{taskStatusLabel(task)}</StatusChip>} meta={`${task.execution_mode} · ${task.total_tool_calls} 次调用`} time={formatTimestamp(task.updated_at)} action={<Link className="button-link" aria-label={`${task.status === "waiting_human_plan" ? "审阅" : "打开"}：${task.task}`} to={task.status === "waiting_human_plan" ? `/runs/${task.run_id}/plan` : `/runs/${task.run_id}`}>{task.status === "waiting_human_plan" ? "审阅" : "打开"}</Link>} />)}
      </div>}
      {tasks?.length === 0 && <EmptyState>暂无研究任务。新建一个需要审批的研究计划开始工作。</EmptyState>}
    </section>
    <Panel title="质量摘要" className="section-gap">质量评估是否可用以实际评估记录为准；任务完成不代表真实研究已验收。<Link className="source-link" to="/system">查看质量与限制说明</Link></Panel>
  </div>;
}
