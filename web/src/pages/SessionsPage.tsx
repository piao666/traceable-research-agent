import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, errorMessage, formatTimestamp, taskStatusLabel, taskStatusTone } from "../api/client";
import { Button, PageHeader, Panel, StatusChip } from "../components/primitives";
import { ResourceState } from "../components/ResourceState";
import { useResource } from "../hooks/useResource";

export function SessionsPage() {
  const list = useResource(api.sessions);
  const navigate = useNavigate();
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function create() {
    if (!title.trim() || busy) return;
    setBusy(true); setError("");
    try { const session = await api.createSession(title.trim()); if (mounted.current) navigate(`/sessions/${session.session_id}`); }
    catch (reason) { setError(errorMessage(reason)); setBusy(false); }
  }
  return <div className="page"><PageHeader title="会话" subtitle="按会话组织研究轮次与关联任务；不自动执行研究" action={<Button variant="secondary" onClick={list.refresh}>刷新</Button>} />
    <Panel title="创建会话"><form className="r5-actions" onSubmit={(event) => { event.preventDefault(); void create(); }}><label className="field">会话名称<input className="input" maxLength={200} value={title} onChange={(event) => setTitle(event.target.value)} /></label><Button type="submit" loading={busy} disabled={!title.trim()}>创建会话</Button></form>{error && <p role="alert" className="error-banner">{error}</p>}</Panel>
    <Panel title="本地会话"><ResourceState resource={list} />{list.data?.length === 0 && <p>暂无会话，请先创建一个会话。</p>}
      <div className="stack">{list.data?.map((session) => <article className="r5-item" key={session.session_id}><h3><Link to={`/sessions/${session.session_id}`}>{session.title || "未命名会话"}</Link></h3><p>{session.turn_count} 条记录 · 更新于 {formatTimestamp(session.updated_at)}</p></article>)}</div>
    </Panel></div>;
}

export function SessionPage() {
  const { sessionId = "" } = useParams();
  return <SessionContent key={sessionId} id={sessionId} />;
}

function SessionContent({ id }: { id: string }) {
  const detail = useResource(useCallback((signal: AbortSignal) => api.session(id, signal), [id]));
  const [offset, setOffset] = useState(0);
  const runs = useResource(useCallback((signal: AbortSignal) => api.listTasks(20, offset, { session_id: id }, signal), [id, offset]));
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  async function rename() {
    if (!title.trim() || busy) return;
    setBusy(true); setError(""); setMessage("");
    try { await api.renameSession(id, title.trim()); setTitle(""); setMessage("名称已更新"); detail.refresh(); }
    catch (reason) { setError(errorMessage(reason)); }
    finally { setBusy(false); }
  }
  return <div className="page"><PageHeader title={detail.data?.title || "会话详情"} subtitle="用户问题与研究摘要留在同一会话；完整证据和报告请进入对应任务" action={<Button variant="secondary" onClick={() => { detail.refresh(); runs.refresh(); }}>刷新</Button>} />
    <Link to="/sessions">返回会话列表</Link><ResourceState resource={detail} />
    {detail.data && <><Panel title="会话操作"><div className="r5-actions"><Link className="button button-primary" to={`/research/new?session_id=${encodeURIComponent(id)}`}>在此会话继续研究</Link><label className="field">新名称<input className="input" value={title} maxLength={200} onChange={(event) => setTitle(event.target.value)} /></label><Button disabled={!title.trim()} loading={busy} onClick={rename}>保存名称</Button></div>{message && <p role="status">{message}</p>}{error && <p role="alert">{error}</p>}</Panel>
      <Panel title="历史轮次">{detail.data.turns.length === 0 && <p>暂无轮次。创建关联研究后，问题将保留在这里。</p>}<div className="stack">{detail.data.turns.map((turn) => <article className="r5-item" key={turn.turn_id}><div className="r5-actions"><StatusChip>{turn.role === "user" ? "用户问题" : turn.role === "agent" ? "研究摘要" : turn.role}</StatusChip><time>{formatTimestamp(turn.created_at)}</time>{turn.run_id && <Link to={`/runs/${encodeURIComponent(turn.run_id)}`}>查看关联任务</Link>}</div><p className="r5-prewrap">{turn.content}</p></article>)}</div></Panel>
      <Panel title="关联研究"><ResourceState resource={runs} />{runs.data?.total === 0 && <p>暂无关联任务。</p>}{runs.data?.tasks.map((run) => <article className="r5-item" key={run.run_id}><Link to={`/runs/${run.run_id}`}>{run.task}</Link> <StatusChip tone={taskStatusTone(run)}>{taskStatusLabel(run)}</StatusChip><p>{formatTimestamp(run.created_at)}</p></article>)}{runs.data && runs.data.total > 20 && <div className="r5-actions"><Button variant="secondary" disabled={offset === 0} onClick={() => setOffset(offset - 20)}>上一页</Button><span>{offset + 1}–{Math.min(offset + 20, runs.data.total)} / {runs.data.total}</span><Button variant="secondary" disabled={offset + 20 >= runs.data.total} onClick={() => setOffset(offset + 20)}>下一页</Button></div>}</Panel>
    </>}
  </div>;
}
