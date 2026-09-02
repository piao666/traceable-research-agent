import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api, type TaskStatusResponse } from "../api/client";
import { RunLayout } from "../components/RunLayout";
import { evidenceFixture, graphFixture, planFixture, taskFixture, traceFixture } from "../test/r4Fixtures";
import { EvidencePage } from "./EvidencePage";
import { ReportPage } from "./ReportPage";
import { RunsPage } from "./RunsPage";
import { WorkbenchPage } from "./WorkbenchPage";
import { PlanReviewPage } from "./PlanReviewPage";

beforeEach(() => {
  vi.stubGlobal("EventSource", undefined);
  HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  HTMLDialogElement.prototype.close = function () { this.open = false; };
  vi.spyOn(api, "getTask").mockResolvedValue(taskFixture);
  vi.spyOn(api, "getPlan").mockResolvedValue(planFixture);
  vi.spyOn(api, "getTraces").mockResolvedValue([traceFixture]);
  vi.spyOn(api, "getEvidence").mockResolvedValue(evidenceFixture);
  vi.spyOn(api, "getProvenance").mockResolvedValue(graphFixture);
  vi.spyOn(api, "getReport").mockResolvedValue({ run_id: "fixture", exists: true, availability: "available", markdown: "# 报告正文\n有依据的结论 [CIT-001-01]", requires_review: false, citation_evaluated: true });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
function show(path = "/runs/fixture") {
  return render(<MemoryRouter initialEntries={[path]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}><Routes>
    <Route path="/runs/new/plan" element={<h1>新 Run 等待审批</h1>} />
    <Route path="/runs/:runId/plan" element={<PlanReviewPage />} />
    <Route path="/runs/:runId" element={<RunLayout />}><Route index element={<WorkbenchPage />} /><Route path="evidence" element={<EvidencePage />} /><Route path="report" element={<ReportPage />} /></Route>
    <Route path="/runs" element={<RunsPage />} />
  </Routes></MemoryRouter>);
}
it("shows persisted failure, metrics limitations and full retry", async () => {
  show();
  await screen.findByText("Fixture read failed");
  expect(screen.getByText("不可评估", { exact: false })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "完整重试" })).toBeEnabled();
  expect(screen.queryByRole("button", { name: "取消任务" })).toBeNull();
});
it("creates a fresh retry only after confirmation and navigates to its review", async () => {
  const retry = vi.spyOn(api, "retryTask").mockResolvedValue({ run_id: "new", status: "waiting_human_plan", status_url: "", trace_url: "", report_url: "" });
  const start = vi.spyOn(api, "startTask");
  show(); fireEvent.click(await screen.findByRole("button", { name: "完整重试" }));
  expect(retry).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "确认完整重试" }));
  await screen.findByText("新 Run 等待审批");
  expect(retry).toHaveBeenCalledTimes(1); expect(start).not.toHaveBeenCalled();
});
it("guards double cancellation and preserves visible cancelled state", async () => {
  vi.mocked(api.getTask).mockResolvedValue({ ...taskFixture, status: "running" });
  let finish!: (value: TaskStatusResponse) => void;
  const cancel = vi.spyOn(api, "cancelTask").mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  show(); fireEvent.click(await screen.findByRole("button", { name: "取消任务" }));
  const button = screen.getByRole("button", { name: "确认取消任务" }); fireEvent.click(button); fireEvent.click(button);
  expect(cancel).toHaveBeenCalledTimes(1);
  vi.mocked(api.getTask).mockResolvedValue({ ...taskFixture, status: "cancelled" });
  await act(async () => finish({ ...taskFixture, status: "cancelled" }));
  await screen.findByText("已取消");
  expect(screen.getByRole("button", { name: "完整重试" })).toBeEnabled();
});
it("shows the actual pending operation before asynchronous confirmation", async () => {
  vi.mocked(api.getTask).mockResolvedValue({ ...taskFixture, status: "waiting_human", current_step: 0 });
  const confirm = vi.spyOn(api, "confirmTask").mockResolvedValue({ run_id: "fixture", status: "running", approved: true, resumed: true, message: "queued" });
  show(); fireEvent.click(await screen.findByRole("button", { name: "批准并继续" }));
  expect(within(screen.getByRole("dialog")).getByText(/fixture.md/)).toBeInTheDocument();
  expect(confirm).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "确认批准并继续" }));
  await waitFor(() => expect(confirm).toHaveBeenCalledWith("fixture", true, ""));
});
it("requires explicit confirmation before starting a pending Run", async () => {
  vi.mocked(api.getTask).mockResolvedValue({ ...taskFixture, status: "pending" });
  const start = vi.spyOn(api, "startTask").mockResolvedValue({ run_id: "fixture", status: "running", status_url: "", report_url: "", trace_url: "", message: "queued", requires_review: false, citation_evaluated: false, execution_mode: "planned", adaptive_gate_pending: false, adaptive_upgrade: false });
  show(); fireEvent.click(await screen.findByRole("button", { name: "启动研究" }));
  expect(start).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "确认启动研究" }));
  await waitFor(() => expect(start).toHaveBeenCalledOnce());
});
it("does not silently retry a failed mutation", async () => {
  vi.spyOn(api, "retryTask").mockRejectedValue(new Error("network unavailable"));
  show(); fireEvent.click(await screen.findByRole("button", { name: "完整重试" }));
  fireEvent.click(screen.getByRole("button", { name: "确认完整重试" }));
  await screen.findByText(/未自动重试/);
  expect(api.retryTask).toHaveBeenCalledTimes(1);
});
it("connects report citations to their exact evidence and Trace", async () => {
  vi.mocked(api.getTask).mockResolvedValue({ ...taskFixture, status: "completed" });
  show("/runs/fixture/report");
  fireEvent.click(await screen.findByRole("link", { name: "[CIT-001-01]" }));
  await screen.findByRole("heading", { name: "CIT-001-01" });
  expect(screen.getAllByText("真实证据片段").length).toBeGreaterThan(0);
  expect(screen.getAllByRole("link", { name: "查看来源 Trace" })[0]).toHaveAttribute("href", "/runs/fixture?trace=trace-one");
});
it("keeps unknown evidence IDs unresolved", async () => {
  show("/runs/fixture/evidence?citation=CIT-999-99");
  await screen.findByText(/找不到引用 CIT-999-99/);
  expect(document.getElementById("citation-CIT-001-01")).not.toHaveClass("selected-evidence");
});
it.each(["not_generated", "missing", "blocked"] as const)("handles unavailable report state %s without a fake report", async (availability) => {
  vi.mocked(api.getReport).mockResolvedValue({ run_id: "fixture", exists: false, availability, markdown: "NOT A REPORT", requires_review: false, citation_evaluated: false });
  show("/runs/fixture/report");
  await screen.findByText(availability === "missing" ? "报告文件丢失" : "研究未通过，报告不可作为成功结果展示");
  expect(screen.queryByText("NOT A REPORT")).toBeNull();
  expect(screen.getByRole("button", { name: "下载 Markdown" })).toBeDisabled();
});
it("reports a download failure without pretending that a file was saved", async () => {
  vi.spyOn(api, "downloadReport").mockRejectedValue(new Error("file missing"));
  show("/runs/fixture/report");
  const button = await screen.findByRole("button", { name: "下载 Markdown" });
  await waitFor(() => expect(button).toBeEnabled()); fireEvent.click(button);
  await screen.findByText("下载失败：file missing");
});
it("shows an evidence API failure without claiming there are zero sources", async () => {
  vi.mocked(api.getEvidence).mockRejectedValue(new Error("offline"));
  show("/runs/fixture/evidence");
  await screen.findByText(/这不等于零证据/);
  expect(screen.queryByText(/当前没有有效来源证据/)).toBeNull();
});
it("keeps report readable but references unresolved when the graph is unavailable", async () => {
  vi.mocked(api.getProvenance).mockRejectedValue(new Error("disabled"));
  show("/runs/fixture/report");
  await screen.findByText(/引用图谱读取失败/);
  expect(screen.getByText("[CIT-001-01]（未解析）")).toBeInTheDocument();
});
it("paginates server results and resets the page when filtering", async () => {
  const list = vi.spyOn(api, "listTasks").mockResolvedValue({ tasks: [], total: 45, limit: 20, offset: 0 });
  show("/runs");
  const next = screen.getByRole("button", { name: "下一页" });
  await waitFor(() => expect(next).toBeEnabled()); fireEvent.click(next);
  await waitFor(() => expect(list).toHaveBeenLastCalledWith(20, 20, { status: "all", q: "" }, expect.any(AbortSignal)));
  fireEvent.click(screen.getByRole("tab", { name: "等待人工" }));
  await waitFor(() => expect(list).toHaveBeenLastCalledWith(20, 0, { status: "waiting", q: "" }, expect.any(AbortSignal)));
});
it("navigates from successful plan approval directly to the workbench", async () => {
  vi.spyOn(api, "reviewPlan").mockResolvedValue({ run_id: "fixture", task: "待批计划", status: "waiting_human_plan", preflight: { ready: true, blockers: [], warnings: [], capabilities: { offline_mode: true, tavily_configured: false, llm_provider: "qwen", llm_configured: false, react_provider: "qwen", react_configured: false, react_enabled: false, deep_research_enabled: false, report_generation_mode: "deterministic", connectivity_verified: false } }, execution_mode: "planned", steps: [], allowed_tools: [], estimated_total_tokens: 0, estimated_cost: 0 });
  vi.spyOn(api, "approvePlan").mockResolvedValue({ run_id: "fixture", status: "running", current_step: 0, total_steps: 0, total_tool_calls: 0, report_url: "", trace_url: "", requires_review: false, citation_evaluated: false, execution_mode: "planned", adaptive_upgrade: false, adaptive_upgrade_failed: false, deepening_pending: false });
  show("/runs/fixture/plan"); fireEvent.click(await screen.findByRole("button", { name: "批准并启动" }));
  await screen.findByRole("heading", { name: "研究详情" });
});
it("shows a missing Run as an error with reload instead of an empty workbench", async () => {
  vi.mocked(api.getTask).mockRejectedValue(new Error("Task run not found"));
  show(); await screen.findByRole("heading", { name: "无法读取研究" });
  expect(screen.getByRole("button", { name: "重新加载" })).toBeEnabled();
  expect(api.getTraces).not.toHaveBeenCalled();
});
