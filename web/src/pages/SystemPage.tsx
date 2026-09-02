import { useCallback, useState } from "react";
import { Link } from "react-router-dom";
import { api, formatTimestamp } from "../api/client";
import { Button, MetricCard, PageHeader, Panel, StatusChip } from "../components/primitives";
import { ResourceState } from "../components/ResourceState";
import { useResource } from "../hooks/useResource";

function RunQuality({ id }: { id: string }) {
  const run = useResource(useCallback((signal: AbortSignal) => api.qualityRun(id, signal), [id]));
  const data = run.data;
  return <Panel title="单任务质量明细"><ResourceState resource={run} />{data && <><Link to={`/runs/${encodeURIComponent(id)}`}>打开任务 {id}</Link>{data.requires_review && <p className="error-banner">历史结果待复核；这些分数不能当作当前可信质量。</p>}<p>评估方式：{data.evaluation_method} · 时间：{formatTimestamp(data.created_at)}</p><dl className="r5-facts"><dt>综合 / 10</dt><dd>{data.overall_score}</dd><dt>相关性 / 10</dt><dd>{data.relevance_score}</dd><dt>事实性启发值 / 1</dt><dd>{data.factual_accuracy}</dd><dt>覆盖度 / 10</dt><dd>{data.coverage_score}</dd><dt>来源质量 / 10</dt><dd>{data.source_quality_score}</dd><dt>可审计性 / 10</dt><dd>{data.auditability_score}</dd><dt>引用数量</dt><dd>{data.citation_count}</dd><dt>T0 / T1 / T2 来源</dt><dd>{data.tier_t0} / {data.tier_t1} / {data.tier_t2}</dd><dt>执行模式 / Skill</dt><dd>{data.execution_mode || "未记录"} / {data.skill_composition || "未记录"}</dd></dl></>}</Panel>;
}

export function SystemPage() {
  const runtime = useResource(api.diagnostics);
  const [days, setDays] = useState(30);
  const stats = useResource(useCallback((signal: AbortSignal) => api.qualityStats(days, signal), [days]));
  const trend = useResource(useCallback((signal: AbortSignal) => api.qualityTrend(days, signal), [days]));
  const [selected, setSelected] = useState("");
  const [input, setInput] = useState("");
  const [refreshVersion, setRefreshVersion] = useState(0);
  const config = runtime.data?.capabilities;
  return <div className="page"><PageHeader title="系统与质量" subtitle="本地运行诊断与可追溯质量指标；读取页面不会发起外部模型或搜索调用" action={<Button variant="secondary" onClick={() => { runtime.refresh(); stats.refresh(); trend.refresh(); setRefreshVersion((value) => value + 1); }}>刷新</Button>} />
    <Panel title="服务与存储"><ResourceState resource={runtime} />{runtime.data && <><p>检查时间：{formatTimestamp(runtime.data.checked_at)}</p>{runtime.data.checks.map((check) => <div className="r5-item" key={check.name}><StatusChip tone={check.status === "ok" ? "success" : "danger"}>{check.status === "ok" ? "检查通过" : "异常"}</StatusChip> <strong>{{ service: "API 服务", database: "数据库", workspace: "工作目录" }[check.name] || check.name}</strong><p>{check.message}</p></div>)}</>}</Panel>
    {runtime.data && config && <Panel title="脱敏配置"><dl className="r5-facts"><dt>默认执行模式</dt><dd>{runtime.data.execution_mode}</dd><dt>离线演示</dt><dd>{config.offline_mode ? "开启" : "关闭"}</dd><dt>Tavily</dt><dd>{config.tavily_configured ? "已配置" : "缺少 TAVILY_API_KEY"}</dd><dt>报告模式</dt><dd>{config.report_generation_mode}</dd><dt>报告模型</dt><dd>{config.llm_provider} · {config.llm_configured ? "已配置" : "未配置"}</dd><dt>ReAct</dt><dd>{config.react_enabled ? "启用" : "禁用"} · {config.react_provider} · {config.react_configured ? "已配置" : "未配置"}</dd><dt>多轮深化</dt><dd>{config.deep_research_enabled ? "启用（目标能力限 ReAct）" : "禁用"}</dd><dt>模型记忆提取</dt><dd>{runtime.data.memory_llm_extraction_enabled ? "启用" : "禁用"}</dd></dl><p>密钥值不返回浏览器。配置存在 ≠ 外部连通 ≠ 真实研究验收通过。目录检查未验证容器重启后的持久化。</p></Panel>}
    <Panel title="质量汇总"><label className="field">统计窗口<select className="input" value={days} onChange={(event) => { setDays(Number(event.target.value)); setSelected(""); }}>{[7, 30, 90, 365].map((value) => <option key={value} value={value}>最近 {value} 天</option>)}</select></label><p>仅汇总当前证据规则下可纳入的研究；历史污染指标被排除。规则启发式评分不是事实准确率，也不能代替逐条核查引用与证据。</p><ResourceState resource={stats} />{stats.data && (stats.data.total_runs === 0 ? <p>不可评估：当前窗口没有可信质量记录，不能把零条记录解释为质量通过。</p> : <><div className="metrics-grid"><MetricCard label="纳入研究数" value={stats.data.total_runs} note="不是所有历史任务数量" /><MetricCard label="平均综合分 / 10" value={stats.data.avg_overall.toFixed(1)} note="规则启发式评分" /></div><h3>最近已评估任务</h3>{(stats.data.trend ?? []).map((run) => <article className="r5-item" key={run.run_id}><Button variant="secondary" onClick={() => setSelected(run.run_id)}>查看 {run.run_id}</Button><p>{run.overall} / 10 · {run.citations} 个引用 · {formatTimestamp(run.created_at)}</p></article>)}</>)}</Panel>
    <Panel title="每日趋势"><ResourceState resource={trend} />{trend.data && <><p>趋势：{{ improving: "上升", declining: "下降", stable: "稳定", insufficient_data: "样本不足" }[trend.data.direction] || trend.data.direction}</p>{(trend.data.trend ?? []).length === 0 ? <p>没有可用趋势数据。</p> : <div className="r5-table-scroll" tabIndex={0} role="region" aria-label="每日质量趋势表"><table className="r5-table"><caption className="sr-only">每日质量趋势（UTC）</caption><thead><tr><th scope="col">日期（UTC）</th><th scope="col">平均分 / 10</th><th scope="col">研究数</th></tr></thead><tbody>{(trend.data.trend ?? []).map((point) => <tr key={point.date}><td>{point.date}</td><td>{point.avg_score}</td><td>{point.count}</td></tr>)}</tbody></table></div>}</>}</Panel>
    <Panel title="按任务编号查阅"><form className="r5-actions" onSubmit={(event) => { event.preventDefault(); setSelected(input.trim()); }}><label className="field">Run ID<input className="input" value={input} onChange={(event) => setInput(event.target.value)} /></label><Button type="submit" disabled={!input.trim()}>读取明细</Button></form><p>未评估或不存在的任务会显示接口错误，不补造分数。</p></Panel>
    {selected && <RunQuality key={`${selected}:${refreshVersion}`} id={selected} />}
  </div>;
}
