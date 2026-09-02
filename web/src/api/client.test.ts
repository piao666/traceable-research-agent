import { api, ApiError, formatTimestamp, taskEventsUrl } from "./client";

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });
it("explains validation locations without displaying raw input or context", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: [
    { loc: ["body", "title"], msg: "String should have at least 1 character", input: "private-input", ctx: { secret: "private-context" } },
  ] }), { status: 422 })));
  const failure = await api.createSession("").catch(reason => reason);
  expect(failure).toBeInstanceOf(ApiError);
  expect(failure.message).toContain("body.title");
  expect(failure.message).toContain("String should have at least 1 character");
  expect(failure.message).not.toContain("private-input");
  expect(failure.message).not.toContain("private-context");
});
it("retains a safe generic message for unrecognized validation errors", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: [null, 5, { input: "private-input" }] }), { status: 422 })));
  await expect(api.createSession("")).rejects.toThrow("请求失败（422）");
});
it("preserves structured backend errors without starting a download", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: { code: "configuration_not_ready", message: "Missing configuration" } }), { status: 409 })));
  try { await api.startTask("fixture"); throw new Error("expected rejection"); }
  catch (reason) {
    expect(reason).toBeInstanceOf(ApiError);
    expect((reason as ApiError).detail).toEqual({ code: "configuration_not_ready", message: "Missing configuration" });
  }
});
it("encodes IDs, search filters and stream cursors", async () => {
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ tasks: [], total: 0 })));
  vi.stubGlobal("fetch", fetch);
  await api.listTasks(20, 40, { status: "waiting", q: "a&b" });
  expect(fetch.mock.calls[0][0]).toBe("/api/tasks?limit=20&offset=40&status=waiting&q=a%26b");
  expect(taskEventsUrl("a/b", "t?x")).toBe("/api/tasks/a%2Fb/events?after_trace_id=t%3Fx");
});
it("interprets naive SQLite UTC timestamps consistently with explicit UTC", () => {
  expect(formatTimestamp("2026-09-02T01:00:00")).toBe(formatTimestamp("2026-09-02T01:00:00Z"));
  expect(formatTimestamp("broken")).toBe("时间格式无效");
});
it("keeps request cancellation active during response body reads", async () => {
  vi.useFakeTimers();
  vi.stubGlobal("fetch", vi.fn(async (_url: string, init: RequestInit) => ({ ok: true, json: () => new Promise((_resolve, reject) => {
    init.signal?.addEventListener("abort", () => reject(new DOMException("cancelled", "AbortError")));
  }) })));
  const controller = new AbortController();
  const pending = api.getTask("fixture", controller.signal);
  const check = expect(pending).rejects.toMatchObject({ name: "AbortError" });
  await Promise.resolve(); controller.abort(); await check;
});
it("surfaces failed file downloads instead of triggering a save", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "Report file missing" }), { status: 404 })));
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  await expect(api.downloadReport("fixture")).rejects.toThrow("Report file missing");
  expect(click).not.toHaveBeenCalled();
});
it("encodes R5 session filters and uses explicit memory mutation verbs", async () => {
  const fetch = vi.fn().mockImplementation(() => Promise.resolve(new Response("{}")));
  vi.stubGlobal("fetch", fetch);
  await api.listTasks(20, 0, { session_id: "a/b&c" });
  expect(fetch.mock.calls[0][0]).toBe("/api/tasks?limit=20&offset=0&session_id=a%2Fb%26c");
  await api.renameSession("a/b", "Title");
  expect(fetch.mock.calls[1][0]).toBe("/api/sessions/a%2Fb");
  expect(fetch.mock.calls[1][1]).toMatchObject({ method: "PATCH", body: JSON.stringify({ title: "Title" }) });
  await api.confirmMemory("m/1", false);
  expect(fetch.mock.calls[2][0]).toBe("/api/memory/m%2F1/confirm");
  expect(fetch.mock.calls[2][1]).toMatchObject({ method: "POST", body: '{"approved":false}' });
  await api.deleteMemory("m1"); await api.clearMemories();
  expect(fetch.mock.calls[3][1].method).toBe("DELETE");
  expect(fetch.mock.calls[4][0]).toBe("/api/memory");
  expect(fetch.mock.calls[4][1].method).toBe("DELETE");
});
