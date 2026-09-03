import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api, type TaskPlanResponse } from "../api/client";
import { RunLayout } from "../components/RunLayout";
import { ExecutionInsights } from "../components/ExecutionInsights";
import { ResearchPolicyNote } from "../components/ResearchPolicyNote";
import { r8PlanFixture } from "../test/r8Fixtures";
import { evidenceFixture, graphFixture, taskFixture, traceFixture } from "../test/r4Fixtures";
import { WorkbenchPage } from "./WorkbenchPage";
import { EvidencePage } from "./EvidencePage";
import { ReportPage } from "./ReportPage";
import { PlanReviewPage } from "./PlanReviewPage";

const planCopy = () => structuredClone(r8PlanFixture);
beforeEach(() => {
  vi.stubGlobal("EventSource", undefined);
  vi.spyOn(api, "getTask").mockResolvedValue({ ...taskFixture, status: "completed", execution_mode: "react" });
  vi.spyOn(api, "getPlan").mockResolvedValue(planCopy());
  vi.spyOn(api, "getTraces").mockResolvedValue([{ ...traceFixture, status: "success", error_message: null }]);
  vi.spyOn(api, "getEvidence").mockResolvedValue(evidenceFixture);
  vi.spyOn(api, "getProvenance").mockResolvedValue({ ...graphFixture,
    report_claims: graphFixture.report_claims.map((claim) => ({ ...claim, origin: "source_excerpt" })) });
  vi.spyOn(api, "getReport").mockResolvedValue({ run_id: "fixture", exists: true, availability: "available",
    markdown: "# 固定测试报告\n摘录 [CIT-001-01]", requires_review: false, citation_evaluated: true });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
function show(path = "/runs/fixture") {
  return render(<MemoryRouter initialEntries={[path]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}><Routes>
    <Route path="/runs/:runId/plan" element={<PlanReviewPage />} />
    <Route path="/runs/:runId" element={<RunLayout />}><Route index element={<WorkbenchPage />} /><Route path="evidence" element={<EvidencePage />} /><Route path="report" element={<ReportPage />} /></Route>
  </Routes></MemoryRouter>);
}
function panel(plan: TaskPlanResponse | null = planCopy()) {
  return render(<MemoryRouter><ExecutionInsights plan={plan} /></MemoryRouter>);
}

it("shows GitHub disabled while non-GitHub sources and completed research stay visible", async () => {
  show();
  await screen.findByText("本任务内禁用");
  expect(screen.getByText(/认证失败/)).toBeInTheDocument();
  expect(screen.getByText("已完成")).toBeInTheDocument();
  expect(screen.getAllByText("可继续选择")).toHaveLength(2);
  expect(screen.getByRole("link", { name: /https:\/\/example.org\/source/ })).toHaveAttribute("href", "https://example.org/source");
  expect(screen.getByText(/候选 URL 不等于有效证据/)).toBeInTheDocument();
});
it("keeps unknown cost and disabled cost cap distinct from free research", () => {
  panel();
  expect(screen.getByText("费用估值（CNY）")).toBeInTheDocument();
  expect(screen.getByText("不可完整估算（存在未配置价格）")).toBeInTheDocument();
  expect(screen.getByText("未启用（不是零费用）")).toBeInTheDocument();
});
it("renders child budget ownership and includes reservations in token count", () => {
  const plan = planCopy(); plan.execution_budget!.root_run_id = "parent-id";
  panel(plan);
  expect(screen.getByRole("link", { name: "查看预算所属父任务" })).toHaveAttribute("href", "/runs/parent-id");
  expect(screen.getByText("12000 / 100000")).toBeInTheDocument();
  expect(screen.getByText(/审批恢复不重置/)).toBeInTheDocument();
});
it.each(["tool_calls", "tokens", "deadline", "llm_price_unconfigured"])("explains total-budget stop %s without pretending partial output is final", (reason) => {
  const plan = planCopy(); plan.execution_budget!.stop_reason = reason;
  panel(plan);
  expect(screen.getByText(/预算停止原因/)).toHaveTextContent("不能用中间报告冒充最终报告");
});
it("does not interpret unavailable plans as zero sources or unlimited budget", () => {
  panel(null);
  expect(screen.getByText(/不等于剩余额度无限/)).toBeInTheDocument();
  expect(screen.getByText(/尚未取得来源队列，不能显示为零来源/)).toBeInTheDocument();
  expect(screen.queryByText(/待抓取 0/)).not.toBeInTheDocument();
});
it("refreshes a failed plan read into actual source and budget snapshots", async () => {
  vi.mocked(api.getPlan).mockRejectedValueOnce(new Error("snapshot unavailable"));
  show(); await screen.findByText(/snapshot unavailable/);
  fireEvent.click(screen.getByRole("button", { name: "刷新状态" }));
  await screen.findByText("3 / 40");
  expect(screen.queryByText(/尚未取得来源队列/)).not.toBeInTheDocument();
});
it("distinguishes cooldown and one blocked input without a retry control", () => {
  const plan = planCopy(); plan.execution_insights!.tools[1] = { name: "tavily_search", status: "cooldown", reason: "cooldown", retry_at: 1788397300, blocked_input_count: 0 };
  panel(plan);
  expect(screen.getByText(/到期只恢复选择资格/)).toBeInTheDocument();
  expect(screen.getByText(/已拦截 1 组重复输入/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /重试/ })).not.toBeInTheDocument();
});
it("uses exact candidate Trace links and renders hostile source text inertly", () => {
  const plan = planCopy(); const source = plan.execution_insights!.source_context.sources[0];
  source.title = "<img src=x onerror=alert(1)>"; source.snippet = "<script>alert(1)</script>"; source.url = "javascript:alert(1)";
  const { container } = panel(plan);
  expect(screen.getByRole("link", { name: "查看候选 Trace trace-one" })).toHaveAttribute("href", "/runs/fixture?trace=trace-one");
  expect(screen.getByText(/来源 URL 不安全/)).toBeInTheDocument();
  expect(container.querySelector("script, img")).toBeNull();
});
it("labels source excerpts and shows snapshot/trace identities on citation navigation", async () => {
  show("/runs/fixture/report");
  await screen.findByText(/本报告包含可追溯的来源摘录/);
  fireEvent.click(screen.getByRole("link", { name: "[CIT-001-01]" }));
  await screen.findByText("来源摘录：");
  expect(screen.getByText(/Snapshot：s1 · Trace：trace-one/)).toBeInTheDocument();
  expect(screen.getByText(/不是对计划目标或综合结论的独立事实核查/)).toBeInTheDocument();
  fireEvent.click(screen.getAllByRole("link", { name: "查看来源 Trace" })[0]);
  await waitFor(() => expect(document.getElementById("trace-trace-one")).toHaveAttribute("open"));
});
it("does not claim an empty explicit permission list allows registry tools", async () => {
  vi.spyOn(api, "reviewPlan").mockResolvedValue({ run_id: "fixture", task: "受限计划", status: "waiting_human_plan",
    source_mode: "real", execution_mode: "react", steps: [], allowed_tools: [], estimated_total_tokens: 0, estimated_cost: 0 });
  show("/runs/fixture/plan");
  await screen.findByText("空名单：不允许执行任何工具");
  expect(screen.getByRole("button", { name: "批准并启动" })).toBeDisabled();
  expect(screen.queryByText("由本地注册表决定")).not.toBeInTheDocument();
});
it("explains planned and mock boundaries without promising dynamic recovery or real evidence", () => {
  render(<ResearchPolicyNote executionMode="planned" sourceMode="mock" />);
  expect(screen.getByText(/Planned 按已批准步骤执行/)).toBeInTheDocument();
  expect(screen.getByText(/不能作为真实联网研究验收/)).toBeInTheDocument();
});
it("preserves cancelled task controls even if a stale budget snapshot exists", async () => {
  vi.mocked(api.getTask).mockResolvedValue({ ...taskFixture, status: "cancelled" });
  show(); await screen.findByRole("heading", { name: "共享执行预算" });
  expect(screen.getByRole("button", { name: "完整重试" })).toBeEnabled();
  expect(screen.queryByRole("button", { name: "启动研究" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "批准并继续" })).not.toBeInTheDocument();
});
