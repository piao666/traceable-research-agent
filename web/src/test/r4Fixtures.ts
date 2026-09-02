import type { EvidenceBundleResponse, ProvenanceBundleResponse, TaskPlanResponse, TaskStatusResponse, ToolTraceResponse } from "../api/client";

export const taskFixture: TaskStatusResponse = {
  run_id: "fixture", task: "核对研究证据", report_type: "summary", source_mode: "real", status: "failed",
  current_step: 1, total_steps: 2, total_tool_calls: 1, total_latency_ms: 100, estimated_cost: 0,
  requires_review: false, citation_evaluated: false, quality_warnings: [],
  created_at: "2026-09-02T01:00:00", updated_at: "2026-09-02T01:01:00",
  research_outcome: { status: "failed", effective_evidence_count: 0 },
  execution_mode: "planned", adaptive_gate_pending: false, adaptive_upgrade: false, adaptive_upgrade_failed: false, deepening_pending: false,
  citation_total: 0, citation_supported: 0, citation_weakly_supported: 0, citation_unsupported: 0, citation_accuracy: 0,
};
export const planFixture: TaskPlanResponse = {
  run_id: "fixture", version: "r4", task: "核对研究证据", source_mode: "real", allowed_tools: ["file_reader"], notes: [],
  adaptive_gate_pending: false, adaptive_upgrade: false, adaptive_upgrade_failed: false, deepening_pending: false, deepening_total_rounds: 0,
  steps: [{ step_no: 1, tool_name: "file_reader", goal: "读取本地资料", arguments: { path: "fixture.md" },
    expected_output: "正文", completion_criteria: "有效正文", risk_level: "high", requires_confirmation: true }],
};
export const traceFixture: ToolTraceResponse = {
  trace_id: "trace-one", run_id: "fixture", step_no: 1, tool_name: "file_reader", status: "failed",
  token_in: 0, token_out: 0, estimated_cost: 0,
  created_at: "2026-09-02T01:00:00", finished_at: "2026-09-02T01:00:01", error_message: "Fixture read failed",
};
export const evidenceFixture: EvidenceBundleResponse = {
  run_id: "fixture", task: "核对研究证据", total_evidence_items: 1, source_groups: [], claims: [], unsupported_claims: [], warnings: [],
  evidence_items: [{ evidence_id: "E001", run_id: "fixture", trace_id: "trace-one", step_no: 1, tool_name: "web_fetcher",
    source_type: "web", source_ref: "https://example.org/source", title: "Fixture Source", snippet: "真实证据片段",
    status: "success", confidence: "medium", metadata: { content_basis: "partial" }, is_mock: false, is_fallback: false }],
};
export const graphFixture: ProvenanceBundleResponse = {
  run_id: "fixture", schema_version: "2", extractor_version: "fixture", status: "ready", integrity: {}, assertions: [], claims: [],
  passages: [{ passage_id: "p1", snapshot_id: "s1", trace_id: "trace-one", text: "真实证据片段", content_basis: "partial" }],
  source_snapshots: [{ snapshot_id: "s1", document_id: "d1" }],
  source_documents: [{ document_id: "d1", canonical_uri: "https://example.org/source", title: "Fixture Source" }],
  report_claims: [{ report_claim_id: "r1", claim_text: "有对应证据的结论" }],
  citations: [{ citation_label: "CIT-001-01", passage_id: "p1", report_claim_id: "r1", edge_id: "edge1" }],
  edges: [{ edge_id: "edge1", relation: "supports" }],
};
