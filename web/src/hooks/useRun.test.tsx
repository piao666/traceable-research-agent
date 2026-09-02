import { act, cleanup, renderHook } from "@testing-library/react";
import { api } from "../api/client";
import { planFixture, taskFixture, traceFixture } from "../test/r4Fixtures";
import { useRun } from "./useRun";

class FakeEvents extends EventTarget {
  static instances: FakeEvents[] = [];
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  constructor(public url: string) { super(); FakeEvents.instances.push(this); }
  close() { this.closed = true; }
  emit(name: string, data: Record<string, unknown>) { this.dispatchEvent(new MessageEvent(name, { data: JSON.stringify({ run_id: "fixture", ...data }) })); }
}
async function flush(ms = 0) { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); }
beforeEach(() => {
  vi.useFakeTimers(); FakeEvents.instances = []; vi.stubGlobal("EventSource", FakeEvents);
  vi.spyOn(api, "getTask").mockResolvedValue({ ...taskFixture, status: "running" });
  vi.spyOn(api, "getPlan").mockResolvedValue(planFixture);
  vi.spyOn(api, "getTraces").mockResolvedValue([traceFixture]);
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("loads persisted snapshots and deduplicates replayed trace events", async () => {
  const { result } = renderHook(() => useRun("fixture")); await flush();
  expect(result.current.traces).toHaveLength(1);
  const event = FakeEvents.instances[0];
  act(() => { event.emit("trace_finished", { trace_id: "trace-one", status: "failed", finished_at: "now" }); event.emit("trace_finished", { trace_id: "trace-one", status: "failed", finished_at: "now" }); });
  await flush(100);
  expect(api.getTraces).toHaveBeenCalledTimes(2);
  expect(result.current.traces).toHaveLength(1);
});
it("reconnects after an error using a persisted trace cursor", async () => {
  renderHook(() => useRun("fixture")); await flush();
  const event = FakeEvents.instances[0];
  act(() => { event.emit("trace_finished", { trace_id: "trace-one", status: "success" }); event.onerror?.(); });
  await flush(1000);
  expect(event.closed).toBe(true);
  expect(FakeEvents.instances.at(-1)?.url).toContain("after_trace_id=trace-one");
});
it("does not interpret stream_timeout as completed and reconnects", async () => {
  const { result } = renderHook(() => useRun("fixture")); await flush();
  act(() => FakeEvents.instances[0].emit("done", { status: "stream_timeout" })); await flush(1000);
  expect(result.current.task?.status).toBe("running");
  expect(FakeEvents.instances).toHaveLength(2);
});
it("closes at terminal state and cannot resurrect cancellation from stale events", async () => {
  const { result } = renderHook(() => useRun("fixture")); await flush();
  vi.mocked(api.getTask).mockResolvedValue({ ...taskFixture, status: "cancelled" });
  act(() => FakeEvents.instances[0].emit("run_status", { status: "completed" })); await flush(100);
  expect(result.current.task?.status).toBe("cancelled");
  expect(FakeEvents.instances[0].closed).toBe(true);
  const count = vi.mocked(api.getTask).mock.calls.length; await flush(10000);
  expect(api.getTask).toHaveBeenCalledTimes(count);
});
it("polls waiting-human state without repeated SSE connections", async () => {
  vi.mocked(api.getTask).mockResolvedValue({ ...taskFixture, status: "waiting_human" });
  const { result } = renderHook(() => useRun("fixture")); await flush(); await flush(5000);
  expect(FakeEvents.instances).toHaveLength(0);
  expect(api.getTask).toHaveBeenCalledTimes(2);
  expect(result.current.connection).toBe("paused");
});
it("falls back to polling without EventSource and cleans up on unmount", async () => {
  vi.stubGlobal("EventSource", undefined);
  const { result, unmount } = renderHook(() => useRun("fixture")); await flush();
  expect(result.current.connection).toBe("polling");
  await flush(5000); expect(api.getTask).toHaveBeenCalledTimes(2);
  unmount(); await flush(10000); expect(api.getTask).toHaveBeenCalledTimes(2);
});
it("does not show a Trace failure as an empty successful response", async () => {
  vi.mocked(api.getTraces).mockRejectedValue(new Error("trace unavailable"));
  const { result } = renderHook(() => useRun("fixture")); await flush();
  expect(result.current.detailErrors).toContain("Trace 读取失败：trace unavailable");
  expect(result.current.task?.run_id).toBe("fixture");
});
it("cleans up the previous stream when switching runs", async () => {
  const { rerender, result } = renderHook(({ id }) => useRun(id), { initialProps: { id: "fixture" } }); await flush();
  const old = FakeEvents.instances[0];
  vi.mocked(api.getTask).mockResolvedValue({ ...taskFixture, run_id: "next", status: "completed" });
  rerender({ id: "next" }); await flush();
  expect(old.closed).toBe(true); expect(result.current.task?.run_id).toBe("next");
});
