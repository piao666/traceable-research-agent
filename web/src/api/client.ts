import type { components } from "./schema";

export type Health = components["schemas"]["HealthResponse"];
export type TaskCreateRequest = components["schemas"]["TaskCreateRequest"];
export type TaskCreateResponse = components["schemas"]["TaskCreateResponse"];
export type TaskListItem = components["schemas"]["TaskListItem"];
export type TaskListResponse = components["schemas"]["TaskListResponse"];
export type TaskStatusResponse = components["schemas"]["TaskStatusResponse"];
export type PlanReviewResponse = components["schemas"]["PlanReviewResponse"];
export type TaskRunResponse = components["schemas"]["TaskRunResponse"];
export type TaskPreflightResponse = components["schemas"]["TaskPreflightResponse"];
export type RuntimeCapabilitiesResponse = components["schemas"]["RuntimeCapabilitiesResponse"];
export type TaskPlanResponse = components["schemas"]["TaskPlanResponse"];
export type ToolTraceResponse = components["schemas"]["ToolTraceResponse"];
export type EvidenceBundleResponse = components["schemas"]["EvidenceBundleResponse"];
export type ProvenanceBundleResponse = components["schemas"]["ProvenanceBundleResponse"];
export type ReportResponse = components["schemas"]["ReportResponse"];
export type SessionDetail = components["schemas"]["SessionDetailResponse"];
export type Memory = components["schemas"]["UserMemoryResponse"];
export type Skill = components["schemas"]["SkillSummary"];
export type Tool = components["schemas"]["ToolInfo"];
type Integrity = Pick<TaskStatusResponse, "requires_review" | "quality_warnings" | "citation_evaluated" | "research_outcome">;

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(message: string, public status: number, public detail?: unknown) { super(message); }
}

async function requestResponse<T>(path: string, read: (response: Response) => Promise<T>, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  if (init?.signal?.aborted) abort();
  init?.signal?.addEventListener("abort", abort, { once: true });
  const timeout = window.setTimeout(abort, 20000);
  try {
    const response = await fetch(`${API_BASE}${path}`, {
      ...init, signal: controller.signal,
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
    if (!response.ok) {
      let detail = `请求失败（${response.status}）`;
      let errorDetail: unknown;
      try {
        const payload = await response.json() as { detail?: unknown };
        errorDetail = payload.detail;
        if (typeof payload.detail === "string") detail = payload.detail;
        else if (Array.isArray(payload.detail)) {
          // Validation errors also contain raw input/ctx; never echo those.
          const messages = payload.detail.slice(0, 5).flatMap((item: unknown) => {
            if (!item || typeof item !== "object" || !("msg" in item) || typeof item.msg !== "string") return [];
            const location = "loc" in item && Array.isArray(item.loc)
              ? item.loc.filter((part: unknown) => typeof part === "string" || typeof part === "number").join(".") : "";
            return [`${location ? `${location}：` : ""}${item.msg}`];
          });
          if (messages.length) detail = `请求参数不符合要求：${messages.join("；")}`;
        }
        else if (payload.detail && typeof payload.detail === "object" && "message" in payload.detail
                 && typeof payload.detail.message === "string") detail = payload.detail.message;
      } catch { /* response is not JSON */ }
      throw new ApiError(detail, response.status, errorDetail);
    }
    // Keep cancellation/deadline active until the body has finished downloading.
    return await read(response);
  } finally {
    window.clearTimeout(timeout);
    init?.signal?.removeEventListener("abort", abort);
  }
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  return requestResponse(path, (response) => response.json() as Promise<T>, init);
}

const taskPath = (id: string) => `/api/tasks/${encodeURIComponent(id)}`;
const post = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });
export function taskEventsUrl(id: string, afterTraceId?: string) {
  return `${API_BASE}${taskPath(id)}/events${afterTraceId ? `?after_trace_id=${encodeURIComponent(afterTraceId)}` : ""}`;
}

export async function downloadArtifact(path: string, filename: string) {
  const blob = await requestResponse(path, (response) => response.blob());
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url; link.download = filename;
  document.body.append(link); link.click(); link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export const api = {
  sessions: (signal?: AbortSignal) => requestJson<components["schemas"]["SessionResponse"][]>("/api/sessions", { signal }),
  session: (id: string, signal?: AbortSignal) => requestJson<SessionDetail>(`/api/sessions/${encodeURIComponent(id)}`, { signal }),
  createSession: (title: string) => requestJson<components["schemas"]["SessionResponse"]>("/api/sessions", post({ title })),
  renameSession: (id: string, title: string) => requestJson<components["schemas"]["SessionResponse"]>(`/api/sessions/${encodeURIComponent(id)}`, { ...post({ title }), method: "PATCH" }),
  memories: (status: string, signal?: AbortSignal) => requestJson<components["schemas"]["MemoryListResponse"]>(`/api/memory${status ? `?status=${encodeURIComponent(status)}` : ""}`, { signal }),
  memoryAudit: (signal?: AbortSignal) => requestJson<components["schemas"]["MemoryAuditResponse"][]>("/api/memory/audit", { signal }),
  confirmMemory: (id: string, approved: boolean) => requestJson<Memory>(`/api/memory/${encodeURIComponent(id)}/confirm`, post({ approved })),
  deleteMemory: (id: string) => requestJson<components["schemas"]["MemoryDeleteResponse"]>(`/api/memory/${encodeURIComponent(id)}`, { method: "DELETE" }),
  clearMemories: () => requestJson<components["schemas"]["MemoryClearResponse"]>("/api/memory", { method: "DELETE" }),
  tools: (signal?: AbortSignal) => requestJson<components["schemas"]["ToolListResponse"]>("/api/tools", { signal }),
  skills: (signal?: AbortSignal) => requestJson<components["schemas"]["SkillListResponse"]>("/api/skills", { signal }),
  skill: (name: string, signal?: AbortSignal) => requestJson<components["schemas"]["SkillDetailResponse"]>(`/api/skills/${encodeURIComponent(name)}`, { signal }),
  diagnostics: (signal?: AbortSignal) => requestJson<components["schemas"]["RuntimeDiagnosticsResponse"]>("/api/runtime/diagnostics", { signal }),
  qualityStats: (days: number, signal?: AbortSignal) => requestJson<components["schemas"]["ImprovementStatsResponse"]>(`/api/improvement/stats?days=${days}`, { signal }),
  qualityTrend: (days: number, signal?: AbortSignal) => requestJson<components["schemas"]["ImprovementTrendResponse"]>(`/api/improvement/trend?days=${days}`, { signal }),
  qualityRun: (id: string, signal?: AbortSignal) => requestJson<components["schemas"]["ImprovementRunResponse"]>(`/api/improvement/runs/${encodeURIComponent(id)}`, { signal }),
  health: (signal?: AbortSignal) => requestJson<Health>("/health", { signal }),
  capabilities: (signal?: AbortSignal) => requestJson<RuntimeCapabilitiesResponse>("/api/runtime/capabilities", { signal }),
  listTasks: (limit = 50, offset = 0, filters: { status?: string; q?: string; session_id?: string } = {}, signal?: AbortSignal) => {
    const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    if (filters.status && filters.status !== "all") params.set("status", filters.status);
    if (filters.q) params.set("q", filters.q);
    if (filters.session_id) params.set("session_id", filters.session_id);
    return requestJson<TaskListResponse>(`/api/tasks?${params}`, { signal });
  },
  getTask: (runId: string, signal?: AbortSignal) => requestJson<TaskStatusResponse>(taskPath(runId), { signal }),
  getPlan: (runId: string, signal?: AbortSignal) => requestJson<TaskPlanResponse>(`${taskPath(runId)}/plan`, { signal }),
  getTraces: (runId: string, signal?: AbortSignal) => requestJson<ToolTraceResponse[]>(`${taskPath(runId)}/trace`, { signal }),
  getEvidence: (runId: string, signal?: AbortSignal) => requestJson<EvidenceBundleResponse>(`${taskPath(runId)}/evidence`, { signal }),
  getProvenance: (runId: string, signal?: AbortSignal) => requestJson<ProvenanceBundleResponse>(`${taskPath(runId)}/evidence/v2`, { signal }),
  getReport: (runId: string, signal?: AbortSignal) => requestJson<ReportResponse>(`/api/reports/${encodeURIComponent(runId)}`, { signal }),
  startTask: (runId: string) => requestJson<components["schemas"]["AsyncRunResponse"]>(`${taskPath(runId)}/run_async`, post({})),
  cancelTask: (runId: string, reason: string) => requestJson<TaskStatusResponse>(`${taskPath(runId)}/cancel`, post({ reason })),
  retryTask: (runId: string) => requestJson<TaskCreateResponse>(`${taskPath(runId)}/retry`, post({ reuse_plan: true, from_failed_step: false })),
  confirmTask: (runId: string, approved: boolean, comment: string) => requestJson<components["schemas"]["TaskConfirmResponse"]>(`${taskPath(runId)}/confirm?start_async=true`, post({ approved, comment, resume: true })),
  downloadReport: (runId: string) => downloadArtifact(`/api/reports/${encodeURIComponent(runId)}/download?format=markdown`, `report-${runId}.md`),
  downloadEvidence: (runId: string) => downloadArtifact(`${taskPath(runId)}/evidence/export/download?format=json`, `evidence-${runId}.json`),
  createTask: (body: TaskCreateRequest) => requestJson<TaskCreateResponse>("/api/tasks", { method: "POST", body: JSON.stringify(body) }),
  reviewPlan: (runId: string, signal?: AbortSignal) => requestJson<PlanReviewResponse>(`/api/tasks/${encodeURIComponent(runId)}/review`, { signal }),
  preflightTask: (runId: string, signal?: AbortSignal) => requestJson<TaskPreflightResponse>(`/api/tasks/${encodeURIComponent(runId)}/preflight`, { signal }),
  approvePlan: (runId: string, approved: boolean, comment?: string) => requestJson<TaskRunResponse>(`/api/tasks/${encodeURIComponent(runId)}/approve-plan?start_async=true`, { method: "POST", body: JSON.stringify({ approved, comment: comment || null }) }),
};

export function statusLabel(status: string): string {
  return ({ running: "运行中", waiting_human: "等待人工", waiting_human_plan: "等待计划", completed: "已完成", failed: "失败", cancelled: "已取消", pending: "待运行" } as Record<string, string>)[status] ?? status;
}

export function statusTone(status: string): "plan" | "running" | "success" | "warning" | "danger" | "neutral" {
  if (status === "running") return "running";
  if (status === "completed") return "success";
  if (status === "waiting_human_plan") return "plan";
  if (status === "waiting_human") return "warning";
  if (status === "failed") return "danger";
  return "neutral";
}

export function taskStatusLabel(task: Integrity & { status: string }): string {
  if (task.requires_review) return "历史结果待复核";
  if (task.status === "completed" && task.quality_warnings?.length) return "已完成 · 有限制";
  return statusLabel(task.status);
}

export function taskStatusTone(task: Integrity & { status: string }): ReturnType<typeof statusTone> {
  return task.requires_review || (task.status === "completed" && task.quality_warnings?.length)
    ? "warning" : statusTone(task.status);
}

export function formatTimestamp(value?: string | null): string {
  if (!value) return "未记录";
  // SQLite returns naive UTC timestamps; do not interpret them as browser-local.
  const timestamp = /(?:Z|[+-]\d\d:\d\d)$/i.test(value) ? value : `${value}Z`;
  const date = new Date(timestamp);
  return Number.isNaN(date.getTime()) ? "时间格式无效" : date.toLocaleString("zh-CN");
}

export function errorMessage(reason: unknown): string {
  return reason instanceof Error ? (reason.name === "AbortError" ? "请求超时或已取消，请刷新重试" : reason.message) : "请求失败，请重试";
}
