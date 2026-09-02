import { useCallback, useState } from "react";
import { Link } from "react-router-dom";
import { api, formatTimestamp, type Memory } from "../api/client";
import { Button, PageHeader, Panel, StatusChip } from "../components/primitives";
import { ConfirmAction } from "../components/ConfirmAction";
import { ResourceState } from "../components/ResourceState";
import { useResource } from "../hooks/useResource";

const states: Record<string, string> = { pending: "待确认", active: "生效", expired: "已过期", superseded: "已替代" };
const actions: Record<string, string> = { confirm: "确认生效", reject: "拒绝并删除", delete: "删除", clear: "清空全部" };
type Action = { kind: "confirm" | "reject" | "delete"; memory: Memory } | { kind: "clear" };

export function MemoryPage() {
  const [status, setStatus] = useState("");
  const list = useResource(useCallback((signal: AbortSignal) => api.memories(status, signal), [status]));
  const audit = useResource(api.memoryAudit);
  const runtime = useResource(api.diagnostics);
  const [action, setAction] = useState<Action | null>(null);
  const [message, setMessage] = useState("");
  async function perform() {
    if (!action) return;
    if (action.kind === "clear") {
      const result = await api.clearMemories(); setMessage(`已清空 ${result.count} 条记忆`);
    } else {
      if (action.kind === "delete") await api.deleteMemory(action.memory.memory_id);
      else await api.confirmMemory(action.memory.memory_id, action.kind === "confirm");
      setMessage(`已${actions[action.kind]}`);
    }
    list.refresh(); audit.refresh();
  }
  return <div className="page"><PageHeader title="记忆" subtitle="本地跨会话偏好；只有确认生效且未过期的记忆参与召回" action={<Button variant="secondary" onClick={() => { list.refresh(); audit.refresh(); runtime.refresh(); }}>刷新</Button>} />
    <Panel title="提取与保留规则"><ResourceState resource={runtime} />{runtime.data && <p>模型记忆提取：{runtime.data.memory_llm_extraction_enabled ? "已启用（配置状态，未验证模型调用）" : "未启用"}。规则提取可能仍运行；并非每次研究都会产生记忆，候选项需确认后生效。</p>}<p>过期状态按有效期即时判断，不改写历史记录。删除仅影响记忆库，不删除源任务、会话或报告；审计只保留动作、编号和数量，不保留已删内容。</p></Panel>
    <Panel title="记忆库"><div className="r5-actions"><label className="field">状态筛选<select className="input" value={status} onChange={(event) => setStatus(event.target.value)}><option value="">全部状态</option>{Object.entries(states).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><Button variant="danger" disabled={!list.data?.total} onClick={() => setAction({ kind: "clear" })}>清空全部记忆</Button></div>
      {message && <p role="status">{message}</p>}<ResourceState resource={list} />{list.data && <p>全库 {list.data.total} 条 · 生效 {list.data.active_count} · 待确认 {list.data.pending_count} · 当前筛选 {list.data.memories.length} 条</p>}
      {list.data?.memories.length === 0 && <p>当前没有记忆记录。无需为填充页面而创建研究。</p>}
      <div className="stack">{list.data?.memories.map((memory) => <article className="r5-item" key={memory.memory_id}><div className="r5-actions"><StatusChip tone={memory.status === "pending" ? "warning" : "neutral"}>{states[memory.status] || memory.status}</StatusChip><span>{memory.kind} · {memory.extraction_method}</span></div><p className="r5-prewrap">{memory.content}</p><p>提取置信度：{memory.confidence.toFixed(2)}（非事实准确率） · 有效期：{memory.valid_until ? formatTimestamp(memory.valid_until) : "未设定"}</p><p>创建于 {formatTimestamp(memory.created_at)}</p>
        <div className="r5-actions">{memory.source_run_id && <Link to={`/runs/${encodeURIComponent(memory.source_run_id)}`}>来源任务</Link>}{memory.source_session_id && <Link to={`/sessions/${encodeURIComponent(memory.source_session_id)}`}>来源会话</Link>}{!memory.source_run_id && !memory.source_session_id && <span>未记录来源关联</span>}
          {memory.status === "pending" && <><Button variant="secondary" onClick={() => setAction({ kind: "confirm", memory })}>确认生效</Button><Button variant="danger" onClick={() => setAction({ kind: "reject", memory })}>拒绝并删除</Button></>}<Button variant="danger" onClick={() => setAction({ kind: "delete", memory })}>删除记忆</Button>
        </div></article>)}</div>
    </Panel>
    <Panel title="近期操作审计"><ResourceState resource={audit} />{audit.data?.length === 0 && <p>暂无新版本记忆操作审计。历史操作不会被补造。</p>}{audit.data && audit.data.length > 0 && <div className="r5-table-scroll" tabIndex={0} role="region" aria-label="记忆操作审计表"><table className="r5-table"><caption className="sr-only">近期记忆操作审计</caption><thead><tr><th scope="col">时间</th><th scope="col">动作</th><th scope="col">范围</th></tr></thead><tbody>{audit.data.map((event) => <tr key={event.event_id}><td>{formatTimestamp(event.created_at)}</td><td>{actions[event.action] || event.action}</td><td>{event.memory_id || "全部状态"} · {event.affected_count} 条</td></tr>)}</tbody></table><p>最多显示最近 50 条；此处不是研究证据。</p></div>}</Panel>
    {action && <ConfirmAction key={`${action.kind}-${"memory" in action ? action.memory.memory_id : "all"}`} title={actions[action.kind]} destructive={action.kind !== "confirm"} target={"memory" in action ? `${action.memory.memory_id}：${action.memory.content.slice(0, 240)}` : undefined} phrase={action.kind === "clear" ? "清空全部记忆" : undefined} description={action.kind === "confirm" ? "此记忆将用于后续研究的偏好召回。请核对内容和来源。" : action.kind === "clear" ? "将永久删除所有状态的全部记忆，不仅是当前筛选结果。无法恢复；源任务、会话、报告与审计保留。" : "将永久删除这条记忆，无法恢复。源任务、会话、报告与操作审计保留。"} perform={perform} close={() => setAction(null)} />}
  </div>;
}
