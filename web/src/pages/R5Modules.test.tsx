import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api, type Memory, type SessionDetail } from "../api/client";
import { SessionsPage, SessionPage } from "./SessionsPage";
import { MemoryPage } from "./MemoryPage";
import { CapabilitiesPage } from "./CapabilitiesPage";
import { SystemPage } from "./SystemPage";
import { NewResearchPage } from "./NewResearchPage";

const time = "2026-09-02T00:00:00";
const session: SessionDetail = { session_id: "s1", title: "会话甲", turns: [], created_at: time, updated_at: time };
const memory: Memory = { memory_id: "m1", content: "偏好中文", kind: "preference", extraction_method: "rule", confidence: 0.8, status: "pending", source_run_id: "r1", source_session_id: "s1", created_at: time, updated_at: time };
const capabilities = { offline_mode: false, tavily_configured: false, llm_provider: "qwen", llm_configured: false, react_provider: "qwen", react_configured: false, react_enabled: true, deep_research_enabled: false, report_generation_mode: "deterministic", connectivity_verified: false };
const diagnostics = { checked_at: time, checks: [{ name: "service", status: "ok" as const, message: "API 请求已响应" }], capabilities, execution_mode: "planned", memory_llm_extraction_enabled: false, mcp_enabled: false, mcp_configured: false };

beforeEach(() => {
  sessionStorage.clear();
  HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  vi.spyOn(api, "diagnostics").mockResolvedValue(diagnostics);
  vi.spyOn(api, "capabilities").mockResolvedValue(capabilities);
  vi.spyOn(api, "sessions").mockResolvedValue([]);
  vi.spyOn(api, "session").mockResolvedValue(session);
  vi.spyOn(api, "listTasks").mockResolvedValue({ tasks: [], total: 0, limit: 20, offset: 0 });
  vi.spyOn(api, "memories").mockResolvedValue({ memories: [], total: 0, active_count: 0, pending_count: 0 });
  vi.spyOn(api, "memoryAudit").mockResolvedValue([]);
  vi.spyOn(api, "tools").mockResolvedValue({ tools: [] });
  vi.spyOn(api, "skills").mockResolvedValue({ skills: [] });
  vi.spyOn(api, "qualityStats").mockResolvedValue({ total_runs: 0, avg_overall: 0, best_score: 0, worst_score: 0, latest_score: 0, trend: [] });
  vi.spyOn(api, "qualityTrend").mockResolvedValue({ trend: [], direction: "insufficient_data" });
  vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("Unexpected real request"))));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
function show(path: string) {
  return render(<MemoryRouter initialEntries={[path]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}><Routes>
    <Route path="/sessions" element={<SessionsPage />} /><Route path="/sessions/:sessionId" element={<SessionPage />} />
    <Route path="/memory" element={<MemoryPage />} /><Route path="/capabilities" element={<CapabilitiesPage />} /><Route path="/system" element={<SystemPage />} />
    <Route path="/research/new" element={<NewResearchPage />} /><Route path="/runs/new/plan" element={<p>计划审阅页</p>} />
  </Routes></MemoryRouter>);
}

it("creates a session without starting research", async () => {
  const create = vi.spyOn(api, "createSession").mockResolvedValue({ ...session, turn_count: 0 });
  const task = vi.spyOn(api, "createTask");
  show("/sessions"); await screen.findByText("暂无会话，请先创建一个会话。");
  fireEvent.change(screen.getByLabelText("会话名称"), { target: { value: "会话甲" } });
  fireEvent.click(screen.getByRole("button", { name: "创建会话" }));
  await screen.findByRole("heading", { name: "会话甲" });
  expect(create).toHaveBeenCalledWith("会话甲"); expect(task).not.toHaveBeenCalled();
});
it("retains creation input after failure and can retry", async () => {
  vi.spyOn(api, "createSession").mockRejectedValue(new Error("creation failed"));
  show("/sessions"); fireEvent.change(screen.getByLabelText("会话名称"), { target: { value: "保留名称" } });
  fireEvent.click(screen.getByRole("button", { name: "创建会话" }));
  await screen.findByText("creation failed"); expect(screen.getByLabelText("会话名称")).toHaveValue("保留名称");
});
it("shows linked history, session-filtered runs, and rename success", async () => {
  vi.mocked(api.session).mockResolvedValue({ ...session, turns: [{ turn_id: "t1", session_id: "s1", role: "user", content: "我的后续问题", run_id: "r1", created_at: time }] });
  const rename = vi.spyOn(api, "renameSession").mockResolvedValue({ ...session, turn_count: 1 });
  show("/sessions/s1"); await screen.findByText("我的后续问题");
  expect(screen.getByRole("link", { name: "查看关联任务" })).toHaveAttribute("href", "/runs/r1");
  expect(api.listTasks).toHaveBeenCalledWith(20, 0, { session_id: "s1" }, expect.any(AbortSignal));
  fireEvent.change(screen.getByLabelText("新名称"), { target: { value: "新标题" } });
  fireEvent.click(screen.getByRole("button", { name: "保存名称" }));
  await screen.findByText("名称已更新"); expect(rename).toHaveBeenCalledWith("s1", "新标题");
});
it("carries session into plan creation and isolates its draft", async () => {
  sessionStorage.setItem("tra:new-task", "独立草稿");
  sessionStorage.setItem("tra:new-task:s1", "会话草稿");
  const create = vi.spyOn(api, "createTask").mockResolvedValue({ run_id: "new", status: "waiting_human_plan", status_url: "", trace_url: "", report_url: "" });
  show("/research/new?session_id=s1"); await screen.findByRole("link", { name: "会话甲" });
  expect(screen.getByLabelText(/研究问题或目标/)).toHaveValue("会话草稿");
  fireEvent.click(screen.getByRole("button", { name: "创建并审阅计划" }));
  await screen.findByText("计划审阅页");
  expect(create).toHaveBeenCalledWith(expect.objectContaining({ task: "会话草稿", session_id: "s1", require_plan_approval: true }));
  expect(sessionStorage.getItem("tra:new-task")).toBe("独立草稿");
  expect(sessionStorage.getItem("tra:new-task:s1")).toBeNull();
});
it("blocks followup creation for an unknown session", async () => {
  vi.mocked(api.session).mockRejectedValue(new Error("Session not found"));
  show("/research/new?session_id=missing"); await screen.findByText("Session not found");
  expect(screen.getByRole("button", { name: "创建并审阅计划" })).toBeDisabled();
});
it("rejects stale responses when navigating between sessions", async () => {
  let finish!: (value: SessionDetail) => void;
  vi.mocked(api.session).mockImplementation((id) => id === "s1" ? new Promise((resolve) => { finish = resolve; }) : Promise.resolve({ ...session, session_id: "s2", title: "会话乙" }));
  vi.mocked(api.sessions).mockResolvedValue([{ ...session, session_id: "s2", title: "会话乙", turn_count: 0 }]);
  show("/sessions/s1"); fireEvent.click(screen.getByRole("link", { name: "返回会话列表" }));
  fireEvent.click(await screen.findByRole("link", { name: "会话乙" })); await screen.findByRole("heading", { name: "会话乙" });
  await act(async () => finish(session)); expect(screen.queryByRole("heading", { name: "会话甲" })).toBeNull();
});
it("shows memory empty state without promising extraction", async () => {
  show("/memory"); await screen.findByText("当前没有记忆记录。无需为填充页面而创建研究。");
  expect(screen.getByText(/并非每次研究都会产生记忆/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "清空全部记忆" })).toBeDisabled();
});
it("confirms a pending memory only after explicit confirmation", async () => {
  vi.mocked(api.memories).mockResolvedValue({ memories: [memory], total: 1, active_count: 0, pending_count: 1 });
  const confirm = vi.spyOn(api, "confirmMemory").mockResolvedValue({ ...memory, status: "active" });
  show("/memory"); fireEvent.click(await screen.findByRole("button", { name: "确认生效" }));
  expect(confirm).not.toHaveBeenCalled();
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "确认操作" }));
  await screen.findByText("已确认生效"); expect(confirm).toHaveBeenCalledWith("m1", true);
});
it("requires typed confirmation to clear all statuses even when filtered", async () => {
  vi.mocked(api.memories).mockResolvedValue({ memories: [memory], total: 8, active_count: 7, pending_count: 1 });
  const clear = vi.spyOn(api, "clearMemories").mockResolvedValue({ deleted: true, count: 8, message: "All 8 memories cleared." });
  show("/memory"); await screen.findByText("偏好中文");
  fireEvent.change(screen.getByLabelText("状态筛选"), { target: { value: "pending" } });
  await screen.findByText("偏好中文"); fireEvent.click(screen.getByRole("button", { name: "清空全部记忆" }));
  const dialog = within(screen.getByRole("dialog")); expect(dialog.getByText(/不仅是当前筛选结果/)).toBeInTheDocument();
  expect(dialog.getByRole("button", { name: "确认操作" })).toBeDisabled(); expect(clear).not.toHaveBeenCalled();
  fireEvent.change(dialog.getByRole("textbox"), { target: { value: "清空全部记忆" } });
  fireEvent.click(dialog.getByRole("button", { name: "确认操作" })); await screen.findByText("已清空 8 条记忆"); expect(clear).toHaveBeenCalledOnce();
});
it("cancel never deletes; a failed delete stays visible without automatic retry", async () => {
  vi.mocked(api.memories).mockResolvedValue({ memories: [memory], total: 1, active_count: 0, pending_count: 1 });
  const remove = vi.spyOn(api, "deleteMemory").mockRejectedValue(new Error("delete failed"));
  show("/memory"); fireEvent.click(await screen.findByRole("button", { name: "删除记忆" }));
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "取消" })); expect(remove).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "删除记忆" })); fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "确认操作" }));
  await screen.findByText("delete failed"); expect(remove).toHaveBeenCalledOnce(); expect(screen.getByText("偏好中文")).toBeInTheDocument();
});
it("does not offer activation for expired memory and safely renders content", async () => {
  vi.mocked(api.memories).mockResolvedValue({ memories: [{ ...memory, status: "expired", content: "<img src=x onerror=alert(1)>" }], total: 1, active_count: 0, pending_count: 0 });
  show("/memory"); await screen.findByText("<img src=x onerror=alert(1)>");
  expect(screen.queryByRole("button", { name: "确认生效" })).toBeNull(); expect(document.querySelector("img")).toBeNull();
});
it("loads Skill details and reports unavailable dependencies without execution controls", async () => {
  vi.mocked(api.skills).mockResolvedValue({ skills: [{ name: "sample", version: "1", description: "模板", status: "valid", required_tools: ["missing"] }] });
  const detail = vi.spyOn(api, "skill").mockResolvedValue({ name: "sample", version: "1", description: "模板", required_tools: ["missing"], parameters: {}, steps: [{ tool: "missing" }] });
  show("/capabilities"); await screen.findByText("缺少或未启用：missing");
  expect(screen.getByText(/MCP 是可选能力/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "查看 sample 详情" })); await screen.findByText("执行步骤");
  expect(detail).toHaveBeenCalledWith("sample", expect.any(AbortSignal));
  expect(screen.queryByRole("button", { name: /运行|保存配置/ })).toBeNull();
});
it("keeps independent panels available when tool loading fails and retries on request", async () => {
  vi.mocked(api.tools).mockRejectedValueOnce(new Error("tool list failed")).mockResolvedValue({ tools: [] });
  show("/capabilities"); await screen.findByText("tool list failed");
  expect(screen.getByText(/MCP 是可选能力/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "重新读取" })); await screen.findByText("没有匹配的已注册工具。");
});
it("never treats no quality records as a passing score", async () => {
  show("/system"); await screen.findByText(/不可评估：当前窗口没有可信质量记录/);
  expect(screen.queryByText("平均综合分 / 10")).toBeNull(); expect(screen.getByText(/配置存在 ≠ 外部连通/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("统计窗口"), { target: { value: "7" } });
  await waitFor(() => expect(api.qualityStats).toHaveBeenCalledWith(7, expect.any(AbortSignal)));
});
it("shows missing evaluation as error, and historical evaluation as requiring review", async () => {
  const detail = vi.spyOn(api, "qualityRun").mockRejectedValueOnce(new Error("No evaluation"));
  show("/system"); fireEvent.change(screen.getByLabelText("Run ID"), { target: { value: "old" } }); fireEvent.click(screen.getByRole("button", { name: "读取明细" }));
  await screen.findByText("No evaluation");
  detail.mockResolvedValue({ run_id: "old", requires_review: true, evaluation_method: "rule_heuristic", overall_score: 9, relevance_score: 9, factual_accuracy: 0.9, coverage_score: 9, source_quality_score: 9, auditability_score: 9, citation_count: 1, tier_t0: 1, tier_t1: 0, tier_t2: 0 });
  fireEvent.click(screen.getByRole("button", { name: "重新读取" })); await screen.findByText(/历史结果待复核；这些分数不能/);
  expect(screen.getByText("事实性启发值 / 1")).toBeInTheDocument();
});
