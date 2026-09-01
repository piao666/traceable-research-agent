import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, statusLabel, statusTone, type Health, type TaskListItem } from "../api/client";
import { Button, MetricCard, PageHeader, Panel, StatusChip, TableRow } from "../components/primitives";

export function OverviewPage() {
  const navigate = useNavigate();
  const [tasks, setTasks] = useState<TaskListItem[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    Promise.all([api.listTasks(50), api.health()])
      .then(([taskResult, healthResult]) => { if (active) { setTasks(taskResult.tasks); setHealth(healthResult); } })
      .catch((reason: unknown) => { if (active) setError(reason instanceof Error ? reason.message : "无法连接本地 API"); });
    return () => { active = false; };
  }, []);

  const counts = useMemo(() => ({
    running: tasks.filter((task) => task.status === "running").length,
    waiting: tasks.filter((task) => task.status === "waiting_human" || task.status === "waiting_human_plan").length,
    completed: tasks.filter((task) => task.status === "completed").length,
    failed: tasks.filter((task) => task.status === "failed").length,
  }), [tasks]);

  return (
    <div className="page" data-figma-screen="32:2">
      <PageHeader title="概览" subtitle="快速掌握本地实例、研究任务与待处理事项" action={<Button onClick={() => navigate("/research/new")}>新建研究</Button>} />
      {error && <div className="error-banner" role="alert">本地 API 未就绪：{error}</div>}
      <section className="metrics-grid" aria-label="研究任务指标">
        <MetricCard label="运行中" value={counts.running} note="本地执行中的任务" />
        <MetricCard label="等待人工" value={counts.waiting} note="计划审批与高风险确认" />
        <MetricCard label="已完成" value={counts.completed} note="当前本地历史记录" />
        <MetricCard label="失败" value={counts.failed} note="可从任务列表重试" />
      </section>
      <section className="two-column">
        <Panel title="需要处理">{counts.waiting ? `${counts.waiting} 个任务等待人工确认` : "暂无等待处理的计划或高风险操作"}</Panel>
        <Panel title="本地环境">{health ? `API ${health.status} · ${health.service} · ${health.execution_mode}` : "正在读取 API、SQLite 与 workspace 状态"}</Panel>
      </section>
      <section className="panel table-card" aria-labelledby="recent-title">
        <h2 id="recent-title" className="table-card-title">最近研究任务</h2>
        <div className="table-scroll" role="table">
          <div className="table-header" role="row"><div>任务</div><div>状态</div><div>模式 / 工具</div><div>更新时间 / 操作</div></div>
          {tasks.slice(0, 4).map((task) => (
            <TableRow key={task.run_id} primary={task.task} status={<StatusChip tone={statusTone(task.status)}>{statusLabel(task.status)}</StatusChip>} meta={`${task.execution_mode} · ${task.total_tool_calls} 次调用`} time={new Date(task.updated_at).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })} action={<button className="button-link" onClick={() => navigate(task.status === "waiting_human_plan" ? `/runs/${task.run_id}/plan` : `/runs/${task.run_id}`)}>{task.status === "waiting_human_plan" ? "审阅" : "打开"}</button>} />
          ))}
          {!tasks.length && <div className="empty-state">暂无研究任务。新建一个需要审批的研究计划开始工作。</div>}
        </div>
      </section>
      <Panel title="质量摘要" className="section-gap">质量评估会在 Run 完成后写入本地 SQLite；此页面不依赖云端同步。</Panel>
    </div>
  );
}
