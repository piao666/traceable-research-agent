import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { expect, it } from "vitest";
import { r8PlanFixture } from "../test/r8Fixtures";
import { ResearchWorkPanel } from "./ResearchWorkPanel";

it("shows acquisition as awaiting confirmation and links the actual recovery trace", () => {
  render(<MemoryRouter><ResearchWorkPanel plan={{ ...r8PlanFixture, research_work: { version: "v1", items: [{
    work_item_id: "work", requirement_id: "r", entity: "AgentAlpha", facet: "application",
    acquisition_status: "acquired", answer_status: "candidate", reason_code: "final_answer_pending", detail: "",
    state_version: 2, actions: [{ operation_id: "op", kind: "read_retained", status: "succeeded", trace_id: "trace-a" }],
  }] } }} /></MemoryRouter>);
  expect(screen.getByText(/候选回答待最终核验/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "查看 Trace" })).toHaveAttribute("href", "/?trace=trace-a");
  expect(screen.queryByText(/最终回答已确认/)).not.toBeInTheDocument();
});
