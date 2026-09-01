import { fireEvent, render, screen } from "@testing-library/react";
import { Button, OptionCard, StatusChip } from "./primitives";

describe("design system primitives", () => {
  it("blocks a loading button and preserves its label", () => {
    render(<Button loading>创建并审阅计划</Button>);
    expect(screen.getByRole("button", { name: "创建并审阅计划" })).toBeDisabled();
  });

  it("exposes option selection semantics", () => {
    const onClick = vi.fn();
    render(<OptionCard title="深度 Web" description="交叉验证" selected onClick={onClick} />);
    const option = screen.getByRole("button", { name: /深度 Web/ });
    expect(option).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(option);
    expect(onClick).toHaveBeenCalledOnce();
  });

  it("maps semantic status variants", () => {
    render(<StatusChip tone="plan">等待计划</StatusChip>);
    expect(screen.getByText("等待计划")).toHaveClass("status-plan");
  });
});
