import { useCallback, useState } from "react";
import { api, type Skill, type Tool } from "../api/client";
import { Button, PageHeader, Panel, StatusChip } from "../components/primitives";
import { ResourceState } from "../components/ResourceState";
import { useResource } from "../hooks/useResource";

function SkillDetail({ name }: { name: string }) {
  const detail = useResource(useCallback((signal: AbortSignal) => api.skill(name, signal), [name]));
  return <div><ResourceState resource={detail} />{detail.data && <><h4>参数</h4><pre className="r5-json" tabIndex={0}>{JSON.stringify(detail.data.parameters, null, 2)}</pre><h4>执行步骤</h4><pre className="r5-json" tabIndex={0}>{JSON.stringify(detail.data.steps, null, 2)}</pre></>}</div>;
}

function SkillItem({ skill, tools }: { skill: Skill; tools: Tool[] | null }) {
  const [open, setOpen] = useState(false);
  const missing = tools ? skill.required_tools.filter((name) => !tools.some((tool) => tool.name === name && tool.enabled)) : [];
  return <article className="r5-item"><h3>{skill.name} <small>{skill.version}</small></h3><p>{skill.description}</p><p>定义状态：{skill.status === "valid" ? "有效" : skill.status}；依赖工具：{skill.required_tools.join("、") || "无"}</p>{skill.error && <p className="error-banner">{skill.error}</p>}{tools === null ? <p>工具清单未读取，无法判断依赖。</p> : missing.length > 0 ? <p className="error-banner">缺少或未启用：{missing.join("、")}</p> : <p>依赖工具已注册并启用；不代表所需密钥、文件或网络已就绪。</p>}<Button variant="secondary" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? "收起" : "查看"} {skill.name} 详情</Button>{open && <SkillDetail name={skill.name} />}</article>;
}

export function CapabilitiesPage() {
  const tools = useResource(api.tools);
  const skills = useResource(api.skills);
  const runtime = useResource(api.diagnostics);
  const [query, setQuery] = useState("");
  const filtered = tools.data?.tools.filter((tool) => `${tool.name} ${tool.description}`.toLowerCase().includes(query.toLowerCase()));
  return <div className="page"><PageHeader title="能力" subtitle="已注册工具与 Skill；只读查看，不运行工具、不修改部署配置" action={<Button variant="secondary" onClick={() => { tools.refresh(); skills.refresh(); runtime.refresh(); }}>刷新</Button>} />
    <Panel title="配置就绪与限制"><ResourceState resource={runtime} />{runtime.data && <><p>Tavily：{runtime.data.capabilities.tavily_configured ? "已配置" : "未配置 TAVILY_API_KEY"}；报告模型 {runtime.data.capabilities.llm_provider}：{runtime.data.capabilities.llm_configured ? "已配置" : "未配置"}；ReAct 模型 {runtime.data.capabilities.react_provider}：{runtime.data.capabilities.react_configured ? "已配置" : "未配置"}。</p><p>远程 MCP：{runtime.data.mcp_enabled ? "已启用" : "未启用"}；服务器配置：{runtime.data.mcp_configured ? "存在" : "未配置"}。MCP 是可选能力，不是基础研究必需依赖。</p><p>当前{runtime.data.capabilities.offline_mode ? "离线演示" : "非离线演示"}。这里只检查本地配置存在性，未验证外部连通性；每次执行还会进行任务级预检。密钥仅在部署端 .env 配置，不在此输入。</p></>}</Panel>
    <Panel title="工具清单"><label className="field">筛选工具<input className="input" value={query} onChange={(event) => setQuery(event.target.value)} /></label><ResourceState resource={tools} />{filtered?.length === 0 && <p>没有匹配的已注册工具。</p>}<div className="stack">{filtered?.map((tool) => <article className="r5-item" key={tool.name}><h3>{tool.name}</h3><p>{tool.description}</p><div className="r5-actions"><StatusChip>{tool.enabled ? "已启用" : "未启用"}</StatusChip><StatusChip tone={tool.risk_level === "high" ? "danger" : "neutral"}>风险：{tool.risk_level}</StatusChip><span>{tool.requires_confirmation ? "需要人工确认" : "无需单独工具确认"} · 超时 {tool.timeout_seconds} 秒</span></div><details><summary>输入与输出约束</summary><p>输入</p><pre className="r5-json" tabIndex={0}>{JSON.stringify(tool.input_schema, null, 2)}</pre><p>输出</p><pre className="r5-json" tabIndex={0}>{JSON.stringify(tool.output_schema, null, 2)}</pre></details></article>)}</div></Panel>
    <Panel title="Skill 清单"><ResourceState resource={skills} />{skills.data?.skills.length === 0 && <p>暂无已加载 Skill。请在部署端检查模板目录；基础工具清单仍可独立使用。</p>}<div className="stack">{skills.data?.skills.map((skill) => <SkillItem key={skill.name} skill={skill} tools={tools.data?.tools ?? null} />)}</div></Panel>
  </div>;
}
