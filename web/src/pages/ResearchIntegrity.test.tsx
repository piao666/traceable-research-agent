import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api, type PlanReviewResponse, type RuntimeCapabilitiesResponse } from "../api/client";
import { NewResearchPage } from "./NewResearchPage";
import { PlanReviewPage } from "./PlanReviewPage";
import { RunsPage } from "./RunsPage";

const capabilities: RuntimeCapabilitiesResponse = {
  research_profile: "deep", research_environment_ready: false, search_provider: "tavily",
  offline_mode: false, tavily_configured: false, llm_provider: "qwen", llm_configured: false,
  react_provider: "qwen", react_configured: false, react_enabled: true,
  deep_research_enabled: false, report_generation_mode: "deterministic", connectivity_verified: false,
};

afterEach(() => { cleanup(); vi.restoreAllMocks(); sessionStorage.clear(); });

it("keeps draft creation available with a concise environment status", async () => {
  const capabilityRequest = vi.spyOn(api, "capabilities").mockResolvedValue(capabilities);
  render(<MemoryRouter><NewResearchPage /></MemoryRouter>);
  expect(screen.queryByText(/尚未配置 TAVILY_API_KEY/)).toBeNull();
  expect(screen.queryByText(/三者并不等同/)).toBeNull();
  expect(screen.queryByText(/报告写入本地 workspace/)).toBeNull();
  expect(screen.queryByText(/source_policy.v2/)).toBeNull();
  expect(screen.queryByText(/本界面不收集账号或密钥/)).toBeNull();
  expect(screen.getByRole("button", { name: "创建并审阅计划" })).toBeEnabled();
  expect(await screen.findByText("研究运行环境：需要配置")).toBeInTheDocument();
  fireEvent.change(screen.getByRole("textbox", { name: /研究问题或目标/ }), { target: { value: "保留我的研究问题" } });
  expect(sessionStorage.getItem("tra:new-task")).toBe("保留我的研究问题");
  expect(capabilityRequest).toHaveBeenCalledTimes(1);
});

it("shows failed real probes as verification failures instead of unverified configuration", async () => {
  vi.spyOn(api, "capabilities").mockResolvedValue({
    ...capabilities,
    llm_configured: true,
    tavily_configured: true,
    items: [
      { name: "web_fetcher", category: "fetch", configured: true, usable: true, mode: "local_http", detail: "可用", checked_at: "2026-09-09T00:00:00Z" },
      { name: "pdf_reader", category: "pdf", configured: true, usable: true, mode: "local", detail: "可用", checked_at: "2026-09-09T00:00:00Z" },
    ],
  });
  vi.spyOn(api, "runtimePreflight").mockResolvedValue({
    checked_at: "2026-09-09T00:00:00Z",
    profile: "deep",
    ready: false,
    verified: true,
    blockers: [{ capability: "llm", error_type: "auth_error", message: "模型验证失败" }],
    warnings: [],
    capabilities: [
      { name: "llm", category: "llm", configured: true, reachable: false, usable: false, mode: "real", detail: "失败", error_type: "auth_error", checked_at: "2026-09-09T00:00:00Z" },
      { name: "tavily", category: "search", configured: true, reachable: false, usable: false, mode: "real", detail: "失败", error_type: "provider_unavailable", checked_at: "2026-09-09T00:00:00Z" },
      { name: "web_fetcher", category: "fetch", configured: true, reachable: false, usable: false, mode: "local_http", detail: "失败", error_type: "dependency_unavailable", checked_at: "2026-09-09T00:00:00Z" },
    ],
  });
  render(<MemoryRouter><NewResearchPage /></MemoryRouter>);
  fireEvent.click(await screen.findByRole("button", { name: "验证真实连接" }));
  expect(await screen.findByText("真实连接验证：未通过；模型验证失败")).toBeInTheDocument();
  expect(screen.getAllByText("验证失败")).toHaveLength(2);
  expect(screen.getByText("验证失败 / 可用")).toBeInTheDocument();
  expect(screen.queryByText("已配置（未验证）")).not.toBeInTheDocument();
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

it("distinguishes missing task requirements from missing API keys", async () => {
  vi.spyOn(api, "reviewPlan").mockResolvedValue({ run_id: "fixture", task: "某股票近10年的涨幅数据",
    status: "waiting_human_plan", source_mode: "real", steps: [], allowed_tools: [], execution_mode: "react",
    estimated_total_tokens: 0, estimated_cost: 0, preflight: { ready: false, warnings: [], capabilities,
      blockers: [{ code: "task_requirements_unresolved", capability: "task", environment_variable: "task",
        message: "请明确统计间隔和复权口径。" }] } });
  const approve = vi.spyOn(api, "approvePlan");
  render(<MemoryRouter initialEntries={["/runs/fixture/plan"]}><Routes>
    <Route path="/runs/:runId/plan" element={<PlanReviewPage />} />
  </Routes></MemoryRouter>);
  await screen.findByText("研究问题尚需补充");
  expect(screen.getByText(/此问题不通过修改密钥解决/)).toBeInTheDocument();
  expect(screen.queryByText(/请在实际部署目录的 .env 配置密钥/)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "批准并启动" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "批准并启动" }));
  expect(approve).not.toHaveBeenCalled();
});

it("labels old completed tasks as requiring review", async () => {
  vi.spyOn(api, "listTasks").mockResolvedValue({ total: 1, limit: 50, offset: 0, tasks: [{
    run_id: "legacy", task: "Old task", status: "completed", execution_mode: "planned",
    report_type: "summary", total_tool_calls: 2, requires_review: true, citation_evaluated: false, estimated_cost: 0,
    run_role: "root", engine_version: "legacy",
    created_at: "2026-09-01T00:00:00Z", updated_at: "2026-09-01T00:00:00Z",
  }] });
  render(<MemoryRouter><RunsPage /></MemoryRouter>);
  expect(await screen.findByText("历史结果待复核")).toBeInTheDocument();
});
