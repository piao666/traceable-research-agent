import type { components } from "./schema";

export type Health = components["schemas"]["HealthResponse"];
export type TaskCreateRequest = components["schemas"]["TaskCreateRequest"];
export type TaskCreateResponse = components["schemas"]["TaskCreateResponse"];
export type TaskListItem = components["schemas"]["TaskListItem"];
export type TaskListResponse = components["schemas"]["TaskListResponse"];
export type TaskStatusResponse = components["schemas"]["TaskStatusResponse"];
export type PlanReviewResponse = components["schemas"]["PlanReviewResponse"];
export type TaskRunResponse = components["schemas"]["TaskRunResponse"];

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message); }
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    let detail = `请求失败（${response.status}）`;
    try {
      const payload = await response.json() as { detail?: string };
      if (payload.detail) detail = payload.detail;
    } catch { /* response is not JSON */ }
    throw new ApiError(detail, response.status);
  }
  return response.json() as Promise<T>;
}

export const api = {
  health: () => requestJson<Health>("/health"),
  listTasks: (limit = 50, offset = 0) => requestJson<TaskListResponse>(`/api/tasks?limit=${limit}&offset=${offset}`),
  getTask: (runId: string) => requestJson<TaskStatusResponse>(`/api/tasks/${encodeURIComponent(runId)}`),
  createTask: (body: TaskCreateRequest) => requestJson<TaskCreateResponse>("/api/tasks", { method: "POST", body: JSON.stringify(body) }),
  reviewPlan: (runId: string) => requestJson<PlanReviewResponse>(`/api/tasks/${encodeURIComponent(runId)}/review`),
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
