import { Link } from "react-router-dom";
import type { TaskPlanResponse } from "../api/client";
import { Panel } from "./primitives";

const answers: Record<string, string> = { unanswered: "待回答", candidate: "候选回答待最终核验", confirmed: "最终回答已确认", acquired: "已取得选择依据" };
const facets: Record<string, string> = { application: "应用场景", mechanism: "原理", framework: "框架", memory: "记忆", evaluation: "评测", selection: "对象选择", answer: "整体回答" };
const actions: Record<string, string> = { reproject: "重选已有正文", read_retained: "补读已有正文", tavily_search: "定向搜索", web_fetcher: "读取来源", dispatch_node: "派发研究分支" };

export function ResearchWorkPanel({ plan }: { plan: TaskPlanResponse | null }) {
  const items = plan?.research_work?.items.filter((item) => item.answer_status !== "superseded") ?? [];
  if (!items.length) return null;
  return <Panel title="研究义务与完成确认">
    <p>逐项核对研究对象、维度、缺口和实际行动。取得正文后仍需校验回答；只有与最终报告关联的证明才显示已确认。</p>
    <ul>{items.map((item) => <li key={item.work_item_id}>
      <strong>{item.entity || "研究问题"} · {facets[item.facet] || item.facet}</strong>：{answers[item.answer_status] || item.answer_status}
      {item.reason_code && <p>当前缺口：{item.detail || item.reason_code}</p>}
      <details><summary>查看对应行动与证明</summary>
        <p>义务：{item.requirement_id}</p>
        {item.actions?.length ? <ol>{item.actions.map((action, index) => <li key={String(action.operation_id || index)}>
          {actions[String(action.kind)] || String(action.kind)} · {String(action.status)}
          {typeof action.trace_id === "string" && <Link className="source-link" to={`?trace=${encodeURIComponent(action.trace_id)}`}>查看 Trace</Link>}
        </li>)}</ol> : <p>尚无恢复行动记录。</p>}
        {typeof item.evidence_refs?.answer_sha256 === "string" && <p>回答版本：<code>{item.evidence_refs.answer_sha256}</code></p>}
      </details>
    </li>)}</ul>
  </Panel>;
}
