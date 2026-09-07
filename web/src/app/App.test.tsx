import { render, screen, waitFor } from "@testing-library/react";
import { App } from "./App";

beforeEach(() => {
  window.history.pushState({}, "", "/");
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/health")) return new Response(JSON.stringify({ status: "ok", service: "traceable-research-agent", phase: "8", execution_mode: "planned", react_enabled: true }), { status: 200, headers: { "Content-Type": "application/json" } });
    return new Response(JSON.stringify({ tasks: [], total: 0, limit: 50, offset: 0 }), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
});

afterEach(() => vi.unstubAllGlobals());

describe("application routes", () => {
  it("renders the overview inside the local-first shell", async () => {
    render(<App />);
    expect(screen.getByText("TRACEABLE RESEARCH")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "概览" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText(/API ok/)).toBeInTheDocument());
    expect(screen.queryByRole("link", { name: "能力" })).toBeNull();
    expect(screen.queryByRole("link", { name: "系统与质量" })).toBeNull();
    expect(screen.queryByText("本地优先 · 单实例")).toBeNull();
    expect(screen.queryByText("质量摘要")).toBeNull();
    expect(screen.queryByText("查看数据库、workspace 与配置诊断")).toBeNull();
  });
});
