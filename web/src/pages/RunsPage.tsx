import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, statusLabel, statusTone, type TaskListItem } from "../api/client";
import { Button, PageHeader, StatusChip, Tab, TableRow } from "../components/primitives";

type Filter = "all" | "running" | "waiting" | "failed";

export function RunsPage() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [tasks, setTasks] = useState<TaskListItem[]>([]);
  const [error, setError] = useState("");
  const filter = (params.get("status") as Filter | null) ?? "all";
  const query = params.get("q") ?? "";

  useEffect(() => { api.listTasks(100).then((result) => setTasks(result.tasks)).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "读取任务失败")); }, []);

  const counts = useMemo(() => ({
    all: tasks.length,
    running: tasks.filter((task) => task.status === "running").length,
    waiting: tasks.filter((task) => task.status.startsWith("waiting_human")).length,
    failed: tasks.filter((task) => task.status === "failed").length,
  }), [tasks]);
  const visible = useMemo(() => tasks.filter((task) => {
    const matchesFilter = filter === "all" || (filter === "waiting" ? task.status.startsWith("waiting_human") : task.status === filter);
    const matchesQuery = !query || `${task.task} ${task.run_id}`.toLowerCase().includes(query.toLowerCase());
    return matchesFilter && matchesQuery;
  }), [tasks, filter, query]);

  function updateParam(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (!value || value === "all") next.delete(key); else next.set(key, value);
    setParams(next, { replace: true });
  }

  return (
    <div className="page" data-figma-screen="32:82">
      <PageHeader title="研究任务" subtitle="筛选、恢复并处理保存在本地 SQLite 的历史 Run" action={<Button onClick={() => navigate("/research/new")}>新建研究</Button>} />
      {error && <div className="error-banner" role="alert">{error}</div>}
      <div className="panel filters" role="tablist" aria-label="任务状态">
        <Tab selected={filter === "all"} onClick={() => updateParam("status", "all")}>全部 {counts.all}</Tab>
        <Tab selected={filter === "running"} onClick={() => updateParam("status", "running")}>运行中 {counts.running}</Tab>
        <Tab selected={filter === "waiting"} onClick={() => updateParam("status", "waiting")}>等待人工 {counts.waiting}</Tab>
        <Tab selected={filter === "failed"} onClick={() => updateParam("status", "failed")}>失败 {counts.failed}</Tab>
        <input className="input search-input" value={query} onChange={(event) => updateParam("q", event.target.value)} placeholder="搜索任务名称或 Run ID" aria-label="搜索研究任务" />
      </div>
      <section className="panel table-card" role="table" aria-label="研究任务列表">
        <div className="table-scroll">
          <div className="table-header" role="row"><div>任务</div><div>状态</div><div>模式 / 工具</div><div>更新时间 / 操作</div></div>
          {visible.map((task) => (
            <TableRow key={task.run_id} primary={task.task} status={<StatusChip tone={statusTone(task.status)}>{statusLabel(task.status)}</StatusChip>} meta={`${task.execution_mode} · ${task.total_tool_calls} 次调用`} time={new Date(task.updated_at).toLocaleString("zh-CN")} action={<button className="button-link" onClick={() => navigate(task.status === "waiting_human_plan" ? `/runs/${task.run_id}/plan` : `/runs/${task.run_id}`)}>{task.status === "waiting_human_plan" ? "审阅" : "打开"}</button>} />
          ))}
          {!visible.length && <div className="empty-state">没有符合当前筛选条件的研究任务。</div>}
        </div>
        <div className="pagination"><span>显示 {visible.length} 项，共 {tasks.length} 项 · 筛选已写入 URL</span><div className="pagination-actions"><Button variant="secondary" disabled>上一页</Button><Button variant="secondary" disabled>下一页</Button></div></div>
      </section>
    </div>
  );
}
