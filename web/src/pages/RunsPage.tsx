import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, errorMessage, formatTimestamp, taskStatusLabel, taskStatusTone, type TaskListItem } from "../api/client";
import { Button, PageHeader, StatusChip, Tab, TableRow } from "../components/primitives";

const filters = { all: "全部", running: "运行中", waiting: "等待人工", failed: "失败", completed: "已完成", cancelled: "已取消", pending: "待运行" };
const PAGE_SIZE = 20;

export function RunsPage() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [tasks, setTasks] = useState<TaskListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const requestedFilter = params.get("status") || "all";
  const filter = Object.hasOwn(filters, requestedFilter) ? requestedFilter : "all";
  const query = params.get("q") ?? "";
  const requestedPage = Number(params.get("page") || 1);
  const page = Number.isSafeInteger(requestedPage) && requestedPage >= 1 ? requestedPage : 1;
  const offset = (page - 1) * PAGE_SIZE;
  useEffect(() => {
    const controller = new AbortController(); let active = true, busy = false;
    setLoading(true); setError(""); setTasks([]); setTotal(0);
    async function load() {
      if (busy) return;
      busy = true;
      try {
        const result = await api.listTasks(PAGE_SIZE, offset, { status: filter, q: query }, controller.signal);
        if (active) { setTasks(result.tasks); setTotal(result.total); setError(""); }
      } catch (reason) { if (active) setError(errorMessage(reason)); }
      finally { busy = false; if (active) setLoading(false); }
    }
    const debounce = window.setTimeout(() => { void load(); }, query ? 250 : 0);
    const interval = window.setInterval(() => { void load(); }, 10000);
    const focus = () => { void load(); };
    window.addEventListener("focus", focus);
    return () => { active = false; controller.abort(); window.clearTimeout(debounce); window.clearInterval(interval); window.removeEventListener("focus", focus); };
  }, [filter, query, offset, revision]);
  function updateParam(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (!value || value === "all") next.delete(key); else next.set(key, value);
    if (key !== "page") next.delete("page");
    setParams(next, { replace: key === "q" });
  }
  return <div className="page" data-figma-screen="32:82">
    <PageHeader title="研究任务" subtitle="服务端筛选与分页 · 每 10 秒同步本地任务" action={<Button onClick={() => navigate("/research/new")}>新建研究</Button>} />
    {error && <div className="error-banner" role="alert">{error}<Button variant="secondary" onClick={() => setRevision((value) => value + 1)}>重新加载</Button></div>}
    <div className="panel task-filters">
      <div className="filter-tabs" role="tablist" aria-label="任务状态">{Object.entries(filters).map(([key, label]) => <Tab key={key} id={`status-${key}`} controls="run-results" selected={filter === key} onClick={() => updateParam("status", key)}>{label}</Tab>)}</div>
      <input className="input" value={query} maxLength={500} onChange={(event) => updateParam("q", event.target.value)} placeholder="搜索所有任务名称或 Run ID" aria-label="搜索研究任务" />
    </div>
    <section className="panel table-card" id="run-results" role="tabpanel" aria-labelledby={`status-${filter}`} aria-busy={loading} tabIndex={0}>
      <div className="table-scroll" role="table" aria-label="研究任务列表"><div className="table-header" role="row">{["任务", "状态", "模式 / 工具", "更新时间 / 操作"].map((label) => <div role="columnheader" key={label}>{label}</div>)}</div>
        {tasks.map((task) => <TableRow key={task.run_id} primary={task.task} status={<StatusChip tone={taskStatusTone(task)}>{taskStatusLabel(task)}</StatusChip>} meta={`${task.execution_mode} · ${task.total_tool_calls} 次调用`} time={formatTimestamp(task.updated_at)} action={<button className="button-link" aria-label={`${task.status === "waiting_human_plan" ? "审阅" : "打开"}：${task.task}`} onClick={() => navigate(task.status === "waiting_human_plan" ? `/runs/${task.run_id}/plan` : `/runs/${task.run_id}`)}>{task.status === "waiting_human_plan" ? "审阅" : "打开"}</button>} />)}
      </div>
      {loading && <p className="empty-state" role="status">正在读取研究任务…</p>}
      {!loading && !error && !tasks.length && <div className="empty-state" role="status">{total && offset ? "当前页没有任务，请返回上一页。" : "没有符合当前筛选条件的研究任务。"}</div>}
      <div className="pagination"><span>{loading ? "正在读取页数与任务数…" : error ? "同步失败，当前结果可能已过期" : `第 ${page} 页 · 本页 ${tasks.length} 项 · 匹配 ${total} 项`}</span><div className="pagination-actions"><Button variant="secondary" disabled={loading || page <= 1} onClick={() => updateParam("page", String(page - 1))}>上一页</Button><Button variant="secondary" disabled={loading || !!error || offset + PAGE_SIZE >= total} onClick={() => updateParam("page", String(page + 1))}>下一页</Button></div></div>
    </section>
  </div>;
}
