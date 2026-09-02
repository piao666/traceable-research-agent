import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { basisLabel, citationTargets, safeExternalUrl } from "../lib/evidence";
import { graphFixture } from "../test/r4Fixtures";
import { SafeMarkdown } from "./SafeMarkdown";

afterEach(cleanup);
function show(markdown: string) {
  return render(<MemoryRouter><SafeMarkdown markdown={markdown} runId="fixture" citations={citationTargets(graphFixture)} /></MemoryRouter>);
}
it("never executes HTML or loads remote images and rejects dangerous URL schemes", () => {
  const { container } = show('<script>alert(1)</script>\n<img src=x onerror=alert(1)>\n[x](javascript:alert) [y](data:text/html,bad) ![image](https://example.org/pixel)');
  expect(container.querySelector("script,img,iframe")).toBeNull();
  expect(container.querySelector('a[href^="javascript:"],a[href^="data:"]')).toBeNull();
  expect(container.textContent).toContain("<script>");
});
it("resolves only exact citation IDs and marks unknown references", () => {
  show("结论 [CIT-001-01]，错误引用 [CIT-002-01]");
  expect(screen.getByRole("link", { name: "[CIT-001-01]" })).toHaveAttribute("href", "/runs/fixture/evidence?citation=CIT-001-01");
  expect(screen.getByText("[CIT-002-01]（未解析）")).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: /CIT-002/ })).toBeNull();
});
it("keeps fenced code literal and renders tables and lists safely", () => {
  show("# 标题\n\n| 来源 | 证据 |\n| --- | --- |\n| A | [CIT-001-01] |\n\n- 条目\n\n```html\n<img src=x> [CIT-001-01]\n```");
  expect(screen.getByRole("table")).toBeInTheDocument();
  expect(screen.getByRole("listitem")).toHaveTextContent("条目");
  expect(screen.getAllByRole("link")).toHaveLength(1);
  expect(screen.getByText("<img src=x> [CIT-001-01]")).toBeInTheDocument();
});
it("rejects credential-bearing and relative links", () => {
  for (const url of ["javascript:alert(1)", "data:text/html,x", "file:///etc/passwd", "//example.org", "https://user:secret@example.org"]) expect(safeExternalUrl(url)).toBeNull();
  expect(safeExternalUrl("https://example.org/path")).toBe("https://example.org/path");
});
it("marks missing passage and duplicate citation identifiers as unresolved", () => {
  expect(citationTargets({ ...graphFixture, passages: [] }).get("CIT-001-01")?.resolved).toBe(false);
  expect(citationTargets({ ...graphFixture, citations: [...graphFixture.citations, ...graphFixture.citations] }).get("CIT-001-01")?.resolved).toBe(false);
});
it("uses the backend full_text contract without treating missing metadata as full text", () => {
  expect(basisLabel("full_text")).toBe("全文来源");
  expect(basisLabel("snippet_only")).toBe("搜索摘要");
  expect(basisLabel(undefined)).toBe("内容范围未标注");
});
