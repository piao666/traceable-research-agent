import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api, type PlanReviewResponse, type RuntimeCapabilitiesResponse } from "../api/client";
import { NewResearchPage } from "./NewResearchPage";
import { PlanReviewPage } from "./PlanReviewPage";
import { RunsPage } from "./RunsPage";

const capabilities: RuntimeCapabilitiesResponse = {
  offline_mode: false, tavily_configured: false, llm_provider: "qwen", llm_configured: false,
  react_provider: "qwen", react_configured: false, react_enabled: true,
  deep_research_enabled: false, report_generation_mode: "deterministic", connectivity_verified: false,
};

afterEach(() => { cleanup(); vi.restoreAllMocks(); sessionStorage.clear(); });

it("keeps draft creation available while disclosing missing keys and separate modes", async () => {
  vi.spyOn(api, "capabilities").mockResolvedValue(capabilities);
  render(<MemoryRouter><NewResearchPage /></MemoryRouter>);
  await screen.findByText(/尚未配置 TAVILY_API_KEY/);
  expect(screen.getByText(/三者并不等同/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "创建并审阅计划" })).toBeEnabled();
  fireEvent.change(screen.getByRole("textbox", { name: /研究问题或目标/ }), { target: { value: "保留我的研究问题" } });
  expect(sessionStorage.getItem("tra:new-task")).toBe("保留我的研究问题");
});

it("blocks approval until the refreshed preflight is ready", async () => {
  const preflight = { ready: false, blockers: [{ code: "missing_configuration", capability: "tavily_search",
    environment_variable: "TAVILY_API_KEY", message: "TAVILY_API_KEY is not configured." }], warnings: [], capabilities };
  vi.spyOn(api, "reviewPlan").mockResolvedValue({ run_id: "fixture", task: "Fixture research",
    status: "waiting_human_plan", steps: [], allowed_tools: [], execution_mode: "planned",
    estimated_total_tokens: 0, estimated_cost: 0, notes: [], preflight } as PlanReviewResponse);
  vi.spyOn(api, "preflightTask").mockResolvedValue({ ...preflight, ready: true, blockers: [] });
  const approve = vi.spyOn(api, "approvePlan");
  render(<MemoryRouter initialEntries={["/runs/fixture/plan"]}><Routes>
    <Route path="/runs/:runId/plan" element={<PlanReviewPage />} />
  </Routes></MemoryRouter>);
  await screen.findByText("当前配置无法启动此计划");
  expect(screen.getByRole("button", { name: "批准并启动" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "拒绝计划" })).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "重新检查配置" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "批准并启动" })).toBeEnabled());
  approve.mockRestore();
});

it("labels old completed tasks as requiring review", async () => {
  vi.spyOn(api, "listTasks").mockResolvedValue({ total: 1, limit: 50, offset: 0, tasks: [{
    run_id: "legacy", task: "Old task", status: "completed", execution_mode: "planned",
    report_type: "summary", total_tool_calls: 2, requires_review: true, citation_evaluated: false, estimated_cost: 0,
    created_at: "2026-09-01T00:00:00Z", updated_at: "2026-09-01T00:00:00Z",
  }] });
  render(<MemoryRouter><RunsPage /></MemoryRouter>);
  expect(await screen.findByText("历史结果待复核")).toBeInTheDocument();
});
