import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import { api, ApiError, type PlanReviewResponse, type TaskListResponse } from "../api/client";
import { AppShell } from "../components/AppShell";
import { Modal } from "../components/Modal";
import { PageBoundary } from "../components/PageBoundary";
import { Button } from "../components/primitives";
import { useFocusTarget } from "../hooks/useFocusTarget";
import { OverviewPage } from "./OverviewPage";
import { RunsPage } from "./RunsPage";
import { PlanReviewPage } from "./PlanReviewPage";
import { NewResearchPage } from "./NewResearchPage";
import { taskFixture } from "../test/r4Fixtures";
import { readDraft, saveDraft, removeDraft } from "../lib/draft";

const capabilities = { offline_mode: true, tavily_configured: false, llm_provider: "qwen", llm_configured: false, react_provider: "qwen", react_configured: false, react_enabled: false, deep_research_enabled: false, report_generation_mode: "deterministic", connectivity_verified: false };
const review: PlanReviewResponse = { run_id: "fixture", task: "测试计划", status: "waiting_human_plan", execution_mode: "planned", steps: [], allowed_tools: [], estimated_total_tokens: 0, estimated_cost: 0, preflight: { ready: true, blockers: [], warnings: [], capabilities } };
const emptyTasks: TaskListResponse = { tasks: [], total: 0, limit: 50, offset: 0 };
beforeEach(() => {
  sessionStorage.clear();
  vi.spyOn(api, "health").mockResolvedValue({ status: "ok", service: "fixture", phase: "r6", execution_mode: "planned", react_enabled: false });
  vi.spyOn(api, "listTasks").mockResolvedValue(emptyTasks);
  vi.spyOn(api, "capabilities").mockResolvedValue(capabilities);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
function show(node: React.ReactNode, path = "/") {
  return render(<MemoryRouter initialEntries={[path]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>{node}</MemoryRouter>);
}

it("does not turn an unfinished overview request into zero tasks or empty state", async () => {
  let finish!: (value: TaskListResponse) => void;
  vi.mocked(api.listTasks).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  show(<OverviewPage />); expect(screen.getAllByText("—")).toHaveLength(4);
  expect(screen.queryByText(/暂无研究任务/)).toBeNull();
  await act(async () => finish(emptyTasks));
  expect(screen.getAllByText("0", { exact: true })).toHaveLength(4);
  expect(screen.getByText(/暂无研究任务/)).toBeInTheDocument();
});
it("keeps the environment panel usable when task loading fails and offers retry", async () => {
  vi.mocked(api.listTasks).mockRejectedValueOnce(new Error("任务接口失败")).mockResolvedValue(emptyTasks);
  show(<OverviewPage />); await screen.findByText("任务接口失败");
  expect(screen.getByText(/API ok/)).toBeInTheDocument(); expect(screen.getAllByText("—")).toHaveLength(4);
  fireEvent.click(screen.getByRole("button", { name: "重新读取" })); await screen.findByText(/暂无研究任务/);
});
it("renders task counts even if the independent health request fails", async () => {
  vi.mocked(api.health).mockRejectedValue(new Error("健康接口失败"));
  vi.mocked(api.listTasks).mockResolvedValue({ ...emptyTasks, tasks: [{ ...taskFixture, status: "running" }], total: 1 });
  show(<OverviewPage />); await screen.findByText("健康接口失败");
  expect(screen.getByText("1", { exact: true })).toBeInTheDocument();
  expect(screen.getByRole("table", { name: "最近研究任务" })).toBeInTheDocument();
  expect(screen.getAllByRole("columnheader")).toHaveLength(4);
});
it("provides roving arrow/Home/End navigation and associated task panel", async () => {
  show(<RunsPage />); const all = screen.getByRole("tab", { name: "全部" }); all.focus();
  fireEvent.keyDown(all, { key: "ArrowRight" });
  const running = screen.getByRole("tab", { name: "运行中" }); expect(running).toHaveFocus();
  expect(running).toHaveAttribute("aria-selected", "true"); expect(all).toHaveAttribute("tabindex", "-1");
  expect(screen.getByRole("tabpanel")).toHaveAttribute("aria-labelledby", running.id);
  fireEvent.keyDown(running, { key: "End" }); expect(screen.getByRole("tab", { name: "待运行" })).toHaveFocus();
  fireEvent.keyDown(document.activeElement!, { key: "Home" }); expect(all).toHaveFocus();
  fireEvent.keyDown(all, { key: "ArrowLeft" }); expect(screen.getByRole("tab", { name: "待运行" })).toHaveFocus();
  await waitFor(() => expect(api.listTasks).toHaveBeenCalled());
});
it("does not show a previous filter total while the new filter is loading", async () => {
  vi.mocked(api.listTasks).mockResolvedValueOnce({ ...emptyTasks, total: 123 });
  show(<RunsPage />); await screen.findByText(/匹配 123 项/);
  vi.mocked(api.listTasks).mockImplementation(() => new Promise(() => {}));
  fireEvent.click(screen.getByRole("tab", { name: "失败" }));
  expect(screen.queryByText(/匹配 123 项/)).toBeNull(); expect(screen.getByText("正在读取页数与任务数…")).toBeInTheDocument();
});
it("names repeated task actions with their target", async () => {
  vi.mocked(api.listTasks).mockResolvedValue({ ...emptyTasks, tasks: [taskFixture], total: 1 });
  show(<RunsPage />); expect(await screen.findByRole("button", { name: "打开：核对研究证据" })).toBeInTheDocument();
});
it("restores plan loading after error without submitting anything", async () => {
  vi.spyOn(api, "reviewPlan").mockRejectedValueOnce(new Error("计划读取失败样本")).mockResolvedValue(review);
  const approve = vi.spyOn(api, "approvePlan");
  show(<Routes><Route path="/runs/:runId/plan" element={<PlanReviewPage />} /></Routes>, "/runs/fixture/plan");
  await screen.findByText("计划读取失败样本"); fireEvent.click(screen.getByRole("button", { name: "重新读取" }));
  await screen.findByText("测试计划"); expect(approve).not.toHaveBeenCalled();
});
it("disables approval when preflight is absent", async () => {
  vi.spyOn(api, "reviewPlan").mockResolvedValue({ ...review, preflight: undefined });
  show(<Routes><Route path="/runs/:runId/plan" element={<PlanReviewPage />} /></Routes>, "/runs/fixture/plan");
  await screen.findByText("尚未取得配置预检结果，不能批准启动。");
  expect(screen.getByRole("button", { name: "批准并启动" })).toBeDisabled();
});
it("syncs a conflicting approval to read-only cancelled state", async () => {
  vi.spyOn(api, "reviewPlan").mockResolvedValue(review);
  vi.spyOn(api, "approvePlan").mockRejectedValue(new ApiError("状态已变化", 409));
  vi.spyOn(api, "getTask").mockResolvedValue({ ...taskFixture, status: "cancelled" });
  show(<Routes><Route path="/runs/:runId/plan" element={<PlanReviewPage />} /></Routes>, "/runs/fixture/plan");
  fireEvent.click(await screen.findByRole("button", { name: "批准并启动" }));
  await screen.findByText("已取消"); expect(screen.getByRole("button", { name: "批准并启动" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "重新读取计划" })).toBeInTheDocument();
});
it("labels a pending rejection honestly and prevents duplicate decisions", async () => {
  vi.spyOn(api, "reviewPlan").mockResolvedValue(review);
  const approve = vi.spyOn(api, "approvePlan").mockImplementation(() => new Promise(() => {}));
  show(<Routes><Route path="/runs/:runId/plan" element={<PlanReviewPage />} /></Routes>, "/runs/fixture/plan");
  const reject = await screen.findByRole("button", { name: "拒绝计划" });
  fireEvent.click(reject); fireEvent.click(reject);
  expect(screen.getByRole("button", { name: "正在拒绝" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "批准并启动" })).toBeDisabled();
  expect(screen.queryByText("正在批准")).toBeNull(); expect(approve).toHaveBeenCalledTimes(1);
  expect(approve).toHaveBeenCalledWith("fixture", false, expect.any(String));
});
it("does not navigate back to an old run when an abandoned approval resolves", async () => {
  vi.spyOn(api, "reviewPlan").mockImplementation(async (runId) => ({ ...review, run_id: runId, task: `计划-${runId}` }));
  let finish!: (value: Awaited<ReturnType<typeof api.approvePlan>>) => void;
  vi.spyOn(api, "approvePlan").mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  show(<><Link to="/runs/second/plan">切换任务</Link><Routes><Route path="/runs/:runId/plan" element={<PlanReviewPage />} /><Route path="/runs/:runId" element={<p>旧工作台</p>} /></Routes></>, "/runs/fixture/plan");
  fireEvent.click(await screen.findByRole("button", { name: "批准并启动" }));
  fireEvent.click(screen.getByRole("link", { name: "切换任务" })); await screen.findByText("计划-second");
  await act(async () => finish({ ...taskFixture, report_url: "/api/reports/fixture", trace_url: "/api/tasks/fixture/trace" }));
  expect(screen.getByText("计划-second")).toBeInTheDocument(); expect(screen.queryByText("旧工作台")).toBeNull();
});
it("connects empty research validation to and focuses the input", async () => {
  show(<NewResearchPage />); fireEvent.click(screen.getByRole("button", { name: "创建并审阅计划" }));
  const input = screen.getByRole("textbox", { name: /研究问题或目标/ });
  expect(input).toHaveFocus(); expect(input).toHaveAttribute("aria-invalid", "true");
  expect(input).toHaveAttribute("aria-describedby", "research-error research-help");
  await screen.findByText(/这里只检查配置是否存在/);
});
it("does not crash or promise saved drafts when browser storage is denied", async () => {
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new DOMException("denied"); });
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new DOMException("denied"); });
  vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => { throw new DOMException("denied"); });
  expect(readDraft("a")).toBe(""); expect(saveDraft("a", "b")).toBe(false); expect(() => removeDraft("a")).not.toThrow();
  show(<NewResearchPage />); await screen.findByText(/浏览器存储不可用/);
  expect(screen.getByRole("button", { name: "创建并审阅计划" })).toBeEnabled();
});
it("allows capability retries while preserving the research question", async () => {
  vi.mocked(api.capabilities).mockRejectedValueOnce(new Error("配置暂不可读")).mockResolvedValue(capabilities);
  show(<NewResearchPage />); fireEvent.change(screen.getByRole("textbox", { name: /研究问题或目标/ }), { target: { value: "保留问题" } });
  await screen.findByText("配置暂不可读"); fireEvent.click(screen.getByRole("button", { name: "重新读取" }));
  await screen.findByText(/这里只检查配置是否存在/); expect(screen.getByRole("textbox", { name: /研究问题或目标/ })).toHaveValue("保留问题");
});

function ModalFixture({ busy = false }: { busy?: boolean }) {
  const [open, setOpen] = useState(false);
  return <><button onClick={() => setOpen(true)}>打开弹窗</button>{open && <Modal title="确认样本" description="操作说明样本" busy={busy} close={() => setOpen(false)}><Button onClick={() => setOpen(false)}>返回</Button></Modal>}</>;
}
it("labels a modal, initially focuses its heading and restores its trigger", () => {
  show(<ModalFixture />); const trigger = screen.getByRole("button", { name: "打开弹窗" }); trigger.focus(); fireEvent.click(trigger);
  const dialog = screen.getByRole("dialog", { name: "确认样本" });
  expect(dialog).toHaveAccessibleDescription("操作说明样本"); expect(screen.getByRole("heading", { name: "确认样本" })).toHaveFocus();
  fireEvent.click(within(dialog).getByRole("button", { name: "返回" })); expect(trigger).toHaveFocus();
});
it("dismisses idle modal on cancel event without performing a mutation", () => {
  show(<ModalFixture />); fireEvent.click(screen.getByRole("button", { name: "打开弹窗" }));
  fireEvent(screen.getByRole("dialog"), new Event("cancel", { bubbles: true, cancelable: true }));
  expect(screen.queryByRole("dialog")).toBeNull();
});
it("retains a busy modal on cancel event", () => {
  show(<ModalFixture busy />); fireEvent.click(screen.getByRole("button", { name: "打开弹窗" }));
  fireEvent(screen.getByRole("dialog"), new Event("cancel", { bubbles: true, cancelable: true }));
  expect(screen.getByRole("dialog")).toHaveAttribute("aria-busy", "true");
});
it("restores focus to main when a modal trigger no longer exists", () => {
  function RemovedTrigger() {
    const [open, setOpen] = useState(false);
    return <main id="main-content" tabIndex={-1}>{!open && <button onClick={() => setOpen(true)}>移除触发器</button>}{open && <Modal title="已移除触发器" description="焦点恢复样本" busy={false} close={() => setOpen(false)}><button onClick={() => setOpen(false)}>关闭</button></Modal>}</main>;
  }
  render(<RemovedTrigger />); const trigger = screen.getByRole("button", { name: "移除触发器" }); trigger.focus(); fireEvent.click(trigger);
  fireEvent.click(screen.getByRole("button", { name: "关闭" })); expect(screen.getByRole("main")).toHaveFocus();
});
it("moves route focus and updates title but leaves filter query focus intact", () => {
  show(<AppShell><Routes><Route path="/" element={<Link to="/runs">前往任务</Link>} /><Route path="/runs" element={<><h1>研究任务</h1><Link to="?status=failed">筛选失败</Link></>} /></Routes></AppShell>);
  expect(screen.getByRole("link", { name: "跳到主要内容" })).toHaveAttribute("href", "#main-content");
  fireEvent.click(screen.getByRole("link", { name: "前往任务" })); expect(screen.getByRole("main")).toHaveFocus();
  expect(document.title).toBe("研究任务 · Traceable Research Agent");
  const filter = screen.getByRole("link", { name: "筛选失败" }); filter.focus(); fireEvent.click(filter); expect(filter).toHaveFocus();
});
function FocusFixture({ revision }: { revision: boolean }) {
  useFocusTarget("passage", revision);
  return <><article id="passage" tabIndex={-1}>证据</article><button>继续阅读</button></>;
}
it("focuses deep-linked evidence once without stealing focus on data refresh", () => {
  const view = render(<FocusFixture revision />); expect(screen.getByText("证据")).toHaveFocus();
  screen.getByRole("button", { name: "继续阅读" }).focus();
  view.rerender(<FocusFixture revision={false} />); view.rerender(<FocusFixture revision />);
  expect(screen.getByRole("button", { name: "继续阅读" })).toHaveFocus();
});
it("keeps a recoverable shell after render exceptions without exposing error data", () => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  let broken = true;
  function Broken() { if (broken) throw new Error("private debug detail"); return <p>页面恢复</p>; }
  show(<PageBoundary><Broken /></PageBoundary>); expect(screen.getByText("页面暂时无法显示")).toBeInTheDocument();
  expect(screen.queryByText("private debug detail")).toBeNull();
  broken = false; fireEvent.click(screen.getByRole("button", { name: "重新加载视图" })); expect(screen.getByText("页面恢复")).toBeInTheDocument();
});
