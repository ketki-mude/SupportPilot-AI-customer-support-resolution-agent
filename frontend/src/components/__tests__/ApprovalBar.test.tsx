import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ApprovalBar, type DecisionResult } from "@/components/ApprovalBar";
import { approval } from "@/test/fixtures";

function setup(props: Partial<Parameters<typeof ApprovalBar>[0]> = {}) {
  const onDecide = vi.fn();
  const view = render(
    <ApprovalBar
      approval={approval()}
      canDecide
      deciding={false}
      result={null}
      onDecide={onDecide}
      {...props}
    />,
  );
  return { onDecide, ...view };
}

describe("ApprovalBar", () => {
  it("shows the amount, the order, and how sure the agent is", () => {
    setup();
    expect(screen.getByText(/Refund \$79\.99/)).toBeInTheDocument();
    expect(screen.getByText("ORD-10001")).toBeInTheDocument();
    expect(screen.getByText("Policy match, agent 92% sure")).toBeInTheDocument();
    expect(screen.getByText(/refund 3\.2 Damaged or defective items/)).toBeInTheDocument();
  });

  it("has exactly one filled button, and it is Approve", () => {
    const { container } = setup();
    const primary = container.querySelectorAll('[data-variant="primary"]');
    expect(primary).toHaveLength(1);
    expect(primary[0]).toHaveTextContent("Approve");
    expect(screen.getByRole("button", { name: "Reject" })).toHaveAttribute("data-variant", "outline");
  });

  it("sends the decision with the optional note", async () => {
    const { onDecide } = setup();
    await userEvent.type(screen.getByPlaceholderText("Add a note (optional)"), "Photos confirm damage");
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(onDecide).toHaveBeenCalledWith("approve", "Photos confirm damage");
    await userEvent.click(screen.getByRole("button", { name: "Reject" }));
    expect(onDecide).toHaveBeenLastCalledWith("reject", "Photos confirm damage");
  });

  it("disables both buttons while a decision is saving", () => {
    setup({ deciding: true });
    expect(screen.getByRole("button", { name: "Saving…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Reject" })).toBeDisabled();
  });

  it("is read-only for people who cannot decide", () => {
    const { container } = setup({ canDecide: false });
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(container.querySelector('[data-variant="primary"]')).toBeNull();
    expect(screen.getByText("Waiting for a supervisor to decide.")).toBeInTheDocument();
  });

  it.each<[Partial<ReturnType<typeof approval>>, string]>([
    [{ status: "approved" }, "Approved. The agent is carrying it out"],
    [{ status: "rejected" }, "Rejected. The customer has been told"],
  ])("shows the result inline once decided", (overrides, text) => {
    setup({ approval: approval(overrides) });
    expect(screen.getByRole("status")).toHaveTextContent(text);
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
  });

  it("shows an API error inline", () => {
    const result: DecisionResult = { kind: "error", message: "This approval was already approved" };
    setup({ result });
    expect(screen.getByRole("alert")).toHaveTextContent("This approval was already approved");
  });

  it("flags proposals without a policy citation", () => {
    const base = approval();
    setup({ approval: { ...base, proposed_action: { ...base.proposed_action, citations: [] } } });
    expect(screen.getByText("No policy citation, agent 92% sure")).toBeInTheDocument();
  });
});
