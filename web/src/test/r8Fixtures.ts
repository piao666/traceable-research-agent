import type { TaskPlanResponse } from "../api/client";
import { planFixture } from "./r4Fixtures";

/** Explicit offline UI samples, never research output. */
export const r8PlanFixture: TaskPlanResponse = {
  ...planFixture, execution_mode: "react", evidence_mapping_version: "trace-source-v2",
  allowed_tools: ["mcp_github_search", "tavily_search", "web_fetcher"],
  execution_budget: {
    version: "shared-budget-v1", root_run_id: "fixture", limits: { max_tool_calls: 40, max_llm_calls: 40,
      max_tokens: 100000, max_seconds: 900, max_estimated_cost: 0, tool_cost_estimate: null, llm_cost_per_million_tokens: null },
    tool_calls: 3, llm_calls: 4, accounted_tokens: 12000, estimated_cost: 0, cost_currency: "CNY", cost_evaluable: false,
    deadline: 1788398100, stop_reason: null,
  },
  execution_insights: {
    version: "execution-insights-v1", sampled_at: 1788397200, source_mode: "real", recovery_recorded: true,
    allowed_tools: ["mcp_github_search", "tavily_search", "web_fetcher"],
    tools: [
      { name: "mcp_github_search", status: "disabled", reason: "auth_error", attempts: 1, remaining_attempts: 2, blocked_input_count: 0 },
      { name: "tavily_search", status: "available", reason: null, attempts: 1, remaining_attempts: 2, blocked_input_count: 0 },
      { name: "web_fetcher", status: "available", reason: "input_blocked", attempts: 1, remaining_attempts: 2, blocked_input_count: 1 },
    ],
    source_context: { version: "source-context-v1", omitted_count: 0, untrusted_content: true,
      gaps: { pending_fetch: 1, failed_fetch: 1, fetched: 1, full_text_missing: 2, no_sources: false },
      sources: [
        { source_id: "source-official", url: "https://example.org/source", title: "官网固定样本", snippet: "固定网页正文，不是真实联网结果。",
          fetch_status: "fetched", content_basis: "full_text", trace_ids: ["trace-one"], run_ids: ["fixture"], tools: ["web_fetcher"], fetch_attempts: 1 },
        { source_id: "source-paper", url: "https://example.net/paper", title: "论文固定样本", snippet: "固定论文摘要",
          fetch_status: "pending", content_basis: "search_snippet", trace_ids: ["trace-search"], run_ids: ["fixture"], tools: ["tavily_search"], fetch_attempts: 0 },
        { source_id: "source-failed", url: "https://example.net/failure", title: "抓取失败固定样本", snippet: "",
          fetch_status: "failed", content_basis: "search_snippet", trace_ids: ["trace-failed"], run_ids: ["fixture"], tools: ["web_fetcher"], fetch_attempts: 1 },
      ],
    },
  },
};
