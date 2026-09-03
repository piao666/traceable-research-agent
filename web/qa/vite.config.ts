import { defineConfig, mergeConfig } from "vite";
import base from "../vite.config";
import { evidenceFixture, graphFixture, planFixture, taskFixture, traceFixture } from "../src/test/r4Fixtures";
import { r8PlanFixture } from "../src/test/r8Fixtures";

// QA-only middleware: no proxy, real data, provider calls or mutation execution.
const time = "2026-09-02T01:00:00Z";
const long = "长文本布局核查-" + "LongUnbrokenSourceReference".repeat(6);
const capability = { offline_mode: true, tavily_configured: false, llm_provider: "qwen", llm_configured: false, react_provider: "qwen", react_configured: false, react_enabled: true, deep_research_enabled: false, report_generation_mode: "deterministic", connectivity_verified: false };
const preflight = { ready: false, blockers: [{ code: "missing_configuration", capability: "tavily_search", environment_variable: "TAVILY_API_KEY", message: "测试配置缺少 TAVILY_API_KEY，不会执行研究。" }], warnings: [], capabilities: capability };
const session = { session_id: "session-fixture", title: long, turn_count: 1, created_at: time, updated_at: time };
export default mergeConfig(base, defineConfig({
  define: { "import.meta.env.VITE_API_BASE_URL": JSON.stringify("") },
  server: { port: 5174, strictPort: true, proxy: {} },
  plugins: [{ name: "isolated-r6-fixtures", configureServer(server) {
    // mergeConfig merges proxy records; explicitly disable inherited API proxy.
    server.config.server.proxy = {};
    server.middlewares.use((request, response, next) => {
      const url = new URL(request.url || "/", "http://localhost");
      if (!url.pathname.startsWith("/api/") && url.pathname !== "/health") return next();
      const scenario = new URL(request.headers.referer || "http://localhost").searchParams.get("qa") || "populated";
      const empty = scenario === "empty";
      const r8 = ["recovery", "budget", "legacy"].includes(scenario);
      const plan = r8 && scenario !== "legacy" ? structuredClone(r8PlanFixture) : planFixture;
      if (scenario === "budget" && plan.execution_budget) plan.execution_budget.stop_reason = "tool_calls";
      const task = { ...taskFixture, task: long, execution_mode: r8 ? "react" : "planned", status: scenario === "waiting" ? "waiting_human" : scenario === "budget" ? "failed" : "completed", requires_review: scenario === "legacy", quality_warnings: ["仅用于界面走查的固定测试数据，不是真实研究。"] };
      let data: unknown;
      let status = 200;
      if (request.method !== "GET") { status = 409; data = { detail: "QA 环境不执行任何写入或研究。" }; }
      else if (scenario === "error") { status = 503; data = { detail: "隔离接口故障样本，请重试" }; }
      else if (url.pathname.endsWith("/events")) { status = 503; data = { detail: "QA 使用轮询，不开启事件流" }; }
      else if (url.pathname === "/health") data = { status: "ok", service: "QA fixture", phase: "r6", execution_mode: "planned" };
      else if (url.pathname === "/api/runtime/capabilities") data = capability;
      else if (url.pathname === "/api/runtime/diagnostics") data = { checked_at: time, capabilities: capability, execution_mode: "planned", memory_llm_extraction_enabled: false, mcp_enabled: false, mcp_configured: false, checks: [{ name: "service", status: "ok", message: "隔离测试服务，非部署状态" }] };
      else if (url.pathname === "/api/tasks") data = { tasks: empty ? [] : [task, { ...task, run_id: "legacy", status: "failed" }], total: empty ? 0 : 2, limit: 20, offset: 0 };
      else if (url.pathname.endsWith("/review")) data = { ...planFixture, task: long, status: "waiting_human_plan", execution_mode: "planned", estimated_total_tokens: 0, estimated_cost: 0, preflight };
      else if (url.pathname.endsWith("/preflight")) data = preflight;
      else if (url.pathname.endsWith("/plan")) data = { ...plan, task: long };
      else if (url.pathname.endsWith("/trace")) data = empty ? [] : [{ ...traceFixture, output_summary: long }];
      else if (url.pathname.endsWith("/evidence/v2")) data = empty ? { ...graphFixture, citations: [] } : r8 ? { ...graphFixture, report_claims: graphFixture.report_claims.map(claim => ({ ...claim, origin: "source_excerpt" })) } : graphFixture;
      else if (url.pathname.endsWith("/evidence")) data = empty ? { ...evidenceFixture, total_evidence_items: 0, evidence_items: [] } : { ...evidenceFixture, evidence_items: evidenceFixture.evidence_items.map(item => ({ ...item, title: long, snippet: long })) };
      else if (url.pathname.startsWith("/api/tasks/")) data = task;
      else if (url.pathname.startsWith("/api/reports/")) data = { run_id: "fixture", exists: !empty && scenario !== "budget", availability: scenario === "budget" ? "blocked" : empty ? "not_generated" : "available", requires_review: scenario === "legacy", citation_evaluated: false, markdown: empty || scenario === "budget" ? "" : `# 测试报告\n\n仅用于界面走查 [CIT-001-01]\n\n${long}\n\n| 来源 | 说明 |\n| --- | --- |\n| ${long} | 固定测试样本 |` };
      else if (url.pathname === "/api/sessions") data = empty ? [] : [session];
      else if (url.pathname.startsWith("/api/sessions/")) data = { ...session, turns: empty ? [] : [{ turn_id: "turn-fixture", session_id: session.session_id, role: "user", content: long, run_id: "fixture", created_at: time }] };
      else if (url.pathname === "/api/memory/audit") data = empty ? [] : [{ event_id: "event-fixture", action: "confirm", memory_id: long, affected_count: 1, created_at: time }];
      else if (url.pathname === "/api/memory") data = { total: empty ? 0 : 1, active_count: 0, pending_count: empty ? 0 : 1, memories: empty ? [] : [{ memory_id: "memory-fixture", kind: "preference", extraction_method: "rule", content: long, status: "pending", confidence: 0.5, created_at: time, updated_at: time }] };
      else if (url.pathname === "/api/tools") data = { tools: empty ? [] : [{ name: "file_reader", description: long, enabled: true, risk_level: "low", requires_confirmation: false, timeout_seconds: 30, input_schema: { path: long }, output_schema: {}, tags: [], metadata: {} }] };
      else if (url.pathname === "/api/skills") data = { skills: empty ? [] : [{ name: "sample_skill", version: "1", description: long, required_tools: ["file_reader"], parameters: {}, status: "valid" }] };
      else if (url.pathname.startsWith("/api/skills/")) data = { name: "sample_skill", description: long, version: "1", required_tools: ["file_reader"], parameters: {}, steps: [] };
      else if (url.pathname === "/api/improvement/stats") data = { total_runs: empty ? 0 : 1, avg_overall: 6, best_score: 6, worst_score: 6, latest_score: 6, trend: empty ? [] : [{ run_id: "fixture", overall: 6, category: "fixture", mode: "planned", citations: 1, created_at: time }] };
      else if (url.pathname === "/api/improvement/trend") data = { direction: "insufficient_data", trend: empty ? [] : [{ date: "2026-09-02", avg_score: 6, count: 1 }] };
      else if (url.pathname.startsWith("/api/improvement/runs/")) data = { run_id: "fixture", requires_review: false, evaluation_method: "rule_heuristic", overall_score: 6, relevance_score: 6, factual_accuracy: 0.6, coverage_score: 6, source_quality_score: 6, auditability_score: 6, citation_count: 1, tier_t0: 1, tier_t1: 0, tier_t2: 0 };
      else { status = 404; data = { detail: "Unknown QA endpoint" }; }
      const send = () => { response.statusCode = status; response.setHeader("Content-Type", "application/json"); response.end(JSON.stringify(data)); };
      if (scenario === "slow") setTimeout(send, 4000); else send();
    });
  } }],
}));
