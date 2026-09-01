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
  });
});
