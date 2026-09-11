import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api, type TaskPlanResponse } from "../api/client";
import { RunLayout } from "../components/RunLayout";
import { ExecutionInsights } from "../components/ExecutionInsights";
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
  vi.spyOn(api, "getResultTrace").mockResolvedValue([{ ...traceFixture, status: "success", error_message: null }]);
  vi.spyOn(api, "getEvidence").mockResolvedValue(evidenceFixture);
  vi.spyOn(api, "getProvenance").mockResolvedValue({ ...graphFixture,
    report_claims: graphFixture.report_claims.map((claim) => ({ ...claim, origin: "source_excerpt" })) });
  vi.spyOn(api, "getResultEvidence").mockResolvedValue({ ...graphFixture,
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

it("hides internal budget and recovery panels while candidate sources stay visible", async () => {
  const plan = planCopy();
  plan.notes = ["内部规划备注样本"];
  vi.mocked(api.getPlan).mockResolvedValue(plan);
  show();
  await screen.findByRole("heading", { name: "候选来源与抓取进度" });
  expect(screen.getByText("已完成")).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "共享执行预算" })).not.toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "工具恢复与许可名单" })).not.toBeInTheDocument();
  expect(screen.queryByText("本任务内禁用")).not.toBeInTheDocument();
  expect(screen.queryByText("内部规划备注样本")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("研究执行边界")).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: /https:\/\/example.org\/source/ })).toHaveAttribute("href", "https://example.org/source");
  expect(screen.getByText(/候选 URL 不等于有效证据/)).toBeInTheDocument();
});
it("renders persisted English integrity warnings in Chinese", async () => {
  vi.mocked(api.getTask).mockResolvedValue({
    ...taskFixture,
    quality_warnings: [
      "The requested result was not established; available text is not proof of goal completion.",
      "Some source pages/documents could not be read; only successfully extracted content supports this report.",
      "ReAct stopped with a limitation; the available evidence does not imply exhaustive research.",
    ],
  });
  show();
  await screen.findByText("尚未取得请求的研究结果；现有文本不能证明研究目标已经完成。");
  expect(screen.getByText("部分来源页面或文档无法读取；本报告仅由成功提取的内容提供支持。")).toBeInTheDocument();
  expect(screen.getByText("ReAct 因执行限制而停止；现有证据不能代表已经完成全面研究。")).toBeInTheDocument();
  expect(screen.queryByText(/The requested result was not established/)).not.toBeInTheDocument();
  expect(screen.queryByText(/Some source pages\/documents could not be read/)).not.toBeInTheDocument();
  expect(screen.queryByText(/ReAct stopped with a limitation/)).not.toBeInTheDocument();
});
it("renders persisted English report warnings in Chinese", async () => {
  vi.mocked(api.getTask).mockResolvedValue({ ...taskFixture, status: "completed" });
  vi.mocked(api.getReport).mockResolvedValue({
    run_id: "fixture",
    exists: true,
    availability: "available",
    markdown: "# 固定测试报告",
    requires_review: false,
    citation_evaluated: true,
    quality_warnings: ["Some research steps failed or were skipped; inspect the persisted Trace before using the report."],
  });
  show("/runs/fixture/report");
  await screen.findByText("部分研究步骤执行失败或被跳过；使用报告前请检查已保存的 Trace。");
  expect(screen.queryByText(/Some research steps failed or were skipped/)).not.toBeInTheDocument();
});
it("does not interpret an unavailable plan as zero sources", () => {
  panel(null);
  expect(screen.getByText(/尚未取得来源队列，不能显示为零来源/)).toBeInTheDocument();
  expect(screen.queryByText(/待抓取 0/)).not.toBeInTheDocument();
});
it("refreshes a failed plan read into the actual source snapshot", async () => {
  vi.mocked(api.getPlan).mockRejectedValueOnce(new Error("snapshot unavailable"));
  show(); await screen.findByText(/snapshot unavailable/);
  fireEvent.click(screen.getByRole("button", { name: "刷新状态" }));
  await screen.findByRole("link", { name: /https:\/\/example.org\/source/ });
  expect(screen.queryByText(/尚未取得来源队列/)).not.toBeInTheDocument();
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
    source_mode: "real", execution_mode: "react", steps: [], allowed_tools: ["tavily_search"], notes: ["规划备注样本"],
    estimated_total_tokens: 0, estimated_cost: 0 });
  show("/runs/fixture/plan");
  await screen.findByText("受限计划");
  expect(screen.getByRole("button", { name: "批准并启动" })).toBeDisabled();
  expect(screen.queryByText("允许工具")).not.toBeInTheDocument();
  expect(screen.queryByText("计划备注")).not.toBeInTheDocument();
  expect(screen.queryByText("tavily_search")).not.toBeInTheDocument();
  expect(screen.queryByText("规划备注样本")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("研究执行边界")).not.toBeInTheDocument();
  expect(screen.queryByText("由本地注册表决定")).not.toBeInTheDocument();
});
it("preserves cancelled task controls even if a stale budget snapshot exists", async () => {
  vi.mocked(api.getTask).mockResolvedValue({ ...taskFixture, status: "cancelled" });
  show(); await screen.findByRole("heading", { name: "候选来源与抓取进度" });
  expect(screen.getByRole("button", { name: "完整重试" })).toBeEnabled();
  expect(screen.queryByRole("button", { name: "启动研究" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "批准并继续" })).not.toBeInTheDocument();
});
