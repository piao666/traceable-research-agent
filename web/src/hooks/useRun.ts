import { useCallback, useEffect, useRef, useState } from "react";
import { api, errorMessage, taskEventsUrl, type TaskPlanResponse, type TaskStatusResponse, type ToolTraceResponse } from "../api/client";

export type ConnectionState = "loading" | "connecting" | "live" | "polling" | "paused" | "closed";
const activeStatuses = new Set(["pending", "running"]);
const finalStatuses = new Set(["completed", "failed", "cancelled"]);
const eventTypes = ["run_status", "trace_created", "trace_finished", "waiting_human", "plan_review", "report_ready", "done"];

export function useRun(runId: string) {
  const [task, setTask] = useState<TaskStatusResponse | null>(null);
  const [plan, setPlan] = useState<TaskPlanResponse | null>(null);
  const [traces, setTraces] = useState<ToolTraceResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [detailErrors, setDetailErrors] = useState<string[]>([]);
  const [connection, setConnection] = useState<ConnectionState>("loading");
  const reloadRef = useRef<() => void>(() => {});
  const refresh = useCallback(() => reloadRef.current(), []);

  useEffect(() => {
    const controller = new AbortController();
    let disposed = false, busy = false, queued = false;
    let status = "", lastTraceId = "", retryDelay = 1000;
    let stream: EventSource | null = null;
    let reconnect: number | undefined, scheduled: number | undefined;
    const seen = new Set<string>();
    setTask(null); setPlan(null); setTraces([]); setError(""); setDetailErrors([]); setLoading(true);

    function closeStream() {
      stream?.close(); stream = null;
      window.clearTimeout(reconnect); reconnect = undefined;
    }
    function queueRefresh() {
      if (scheduled !== undefined) return;
      scheduled = window.setTimeout(() => { scheduled = undefined; void load(); }, 100);
    }
    function recover() {
      closeStream();
      if (disposed || !activeStatuses.has(status)) return;
      setConnection("polling");
      reconnect = window.setTimeout(() => { reconnect = undefined; connect(); }, retryDelay);
      retryDelay = Math.min(retryDelay * 2, 30000);
    }
    function connect() {
      if (disposed || stream || reconnect !== undefined || !activeStatuses.has(status)) return;
      if (typeof EventSource === "undefined") { setConnection("polling"); return; }
      setConnection("connecting");
      try { stream = new EventSource(taskEventsUrl(runId, lastTraceId)); }
      catch { recover(); return; }
      stream.onopen = () => { if (!disposed) { retryDelay = 1000; setConnection("live"); } };
      stream.onerror = recover;
      for (const name of eventTypes) stream.addEventListener(name, (event) => {
        if (disposed) return;
        const message = event as MessageEvent<string>;
        let data: Record<string, unknown>;
        try { data = JSON.parse(message.data) as Record<string, unknown>; } catch { return; }
        if (!data || data.run_id !== runId) return;
        if (name === "done" && data.status === "stream_timeout") { recover(); queueRefresh(); return; }
        // Only persisted Trace IDs can resume a stream; heartbeats/status IDs cannot.
        if (typeof data.trace_id === "string") {
          lastTraceId = data.trace_id;
          const version = `${data.trace_id}:${name}:${String(data.status)}:${String(data.finished_at)}`;
          if (seen.has(version)) return;
          if (seen.size >= 2000) seen.clear();
          seen.add(version);
        }
        if (name === "done") closeStream();
        // Events invalidate the view; HTTP snapshots remain authoritative. This
        // prevents a delayed/replayed event from resurrecting a cancelled run.
        queueRefresh();
      });
    }
    async function load() {
      if (disposed) return;
      if (busy) { queued = true; return; }
      busy = true;
      try {
        const snapshot = await api.getTask(runId, controller.signal);
        if (disposed) return;
        status = snapshot.status;
        setTask(snapshot); setError("");
        if (activeStatuses.has(status)) connect();
        else { closeStream(); setConnection(finalStatuses.has(status) ? "closed" : "paused"); }
        const results = await Promise.allSettled([api.getPlan(runId, controller.signal), api.getTraces(runId, controller.signal)]);
        if (disposed) return;
        const errors: string[] = [];
        if (results[0].status === "fulfilled") setPlan(results[0].value);
        else { setPlan(null); errors.push(`计划读取失败：${errorMessage(results[0].reason)}`); }
        if (results[1].status === "fulfilled") {
          const unique = new Map(results[1].value.map((row) => [row.trace_id, row]));
          setTraces([...unique.values()]);
        } else { setTraces([]); errors.push(`Trace 读取失败：${errorMessage(results[1].reason)}`); }
        setDetailErrors(errors);
      } catch (reason) {
        if (!disposed) { status = ""; setError(errorMessage(reason)); closeStream(); setConnection("polling"); }
      } finally {
        busy = false;
        if (!disposed) {
          setLoading(false);
          if (queued) { queued = false; queueRefresh(); }
        }
      }
    }
    reloadRef.current = () => { void load(); };
    void load();
    // Also polls waiting-human runs to notice actions from another tab. Final
    // runs have neither stream nor timer-driven requests.
    const poll = window.setInterval(() => { if (!finalStatuses.has(status)) void load(); }, 5000);
    const onFocus = () => { void load(); };
    window.addEventListener("focus", onFocus);
    return () => {
      disposed = true; controller.abort(); closeStream();
      window.clearInterval(poll); window.clearTimeout(scheduled);
      window.removeEventListener("focus", onFocus); reloadRef.current = () => {};
    };
  }, [runId]);

  return { task, plan, traces, loading, error, detailErrors, connection, refresh };
}
