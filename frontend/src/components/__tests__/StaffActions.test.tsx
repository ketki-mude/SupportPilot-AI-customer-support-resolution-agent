import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MIN_OVERRIDE_REASON, StaffActions } from "@/components/StaffActions";
import { api } from "@/lib/api";
import type { ActionCheck, Order, Ticket } from "@/lib/types";
import { actionCheck, message, order, ticket } from "@/test/fixtures";

function setup(props: { ticket?: Ticket; order?: Order | null; mode?: "handle" | "reply"; busy?: boolean } = {}) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <StaffActions
        ticket={props.ticket ?? ticket()}
        order={props.order === undefined ? order() : props.order}
        mode={props.mode ?? "handle"}
        busy={props.busy}
      />
    </QueryClientProvider>,
  );
}

const OVERRIDE: ActionCheck = actionCheck({
  verdict: "override",
  rule: "over_supervisor_limit",
  reason: "$1299.00 is above the supervisor limit.",
  amount: "1299.00",
  order_id: "ORD-10006",
  skus: ["SKU-LT-140"],
});

describe("StaffActions", () => {
  let check: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    check = vi.spyOn(api, "checkStaffAction").mockResolvedValue(actionCheck());
  });
  afterEach(() => vi.restoreAllMocks());

  it("acts within the rules with one filled button", async () => {
    const take = vi.spyOn(api, "takeStaffAction").mockResolvedValue({
      ticket: ticket({ status: "resolved" }),
      message: message({ sender: "staff" }),
      check: actionCheck(),
      reference: "re_1",
      amount: "29.98",
    });
    const { container } = setup();
    const primary = await screen.findByRole("button", { name: "Refund $29.98" });
    expect(screen.getByText(/Within the rules\./)).toBeInTheDocument();
    expect(container.querySelectorAll('[data-variant="primary"]')).toHaveLength(1);

    await userEvent.click(primary);
    expect(take).toHaveBeenCalledWith("t1", { action: "refund", skus: [], note: null });
    expect(await screen.findByRole("status")).toHaveTextContent("Refunded $29.98. The customer has been told.");
  });

  it("acts once on a double click", async () => {
    const take = vi.spyOn(api, "takeStaffAction").mockResolvedValue({
      ticket: ticket({ status: "resolved" }),
      message: message({ sender: "staff" }),
      check: actionCheck(),
      reference: "re_1",
      amount: "29.98",
    });
    setup();
    await userEvent.dblClick(await screen.findByRole("button", { name: "Refund $29.98" }));
    expect(take).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole("status")).toHaveTextContent("Refunded $29.98.");
  });

  it("checks just the chosen items", async () => {
    setup();
    await screen.findByRole("button", { name: "Refund $29.98" });
    await userEvent.click(screen.getByLabelText(/Slim phone case/));
    await waitFor(() => expect(check).toHaveBeenLastCalledWith("t1", { action: "refund", skus: ["SKU-SP-020"] }));
    await userEvent.click(screen.getByLabelText(/Tempered glass screen protector/));
    expect(screen.getByText("Choose at least one item.")).toBeInTheDocument();
  });

  it("needs a real reason before an override", async () => {
    check.mockResolvedValue(OVERRIDE);
    const take = vi.spyOn(api, "takeStaffAction").mockResolvedValue({
      ticket: ticket({ status: "resolved" }),
      message: message({ sender: "staff" }),
      check: OVERRIDE,
      reference: "re_2",
      amount: "1299.00",
    });
    setup({ order: order({ order_id: "ORD-10006", items: [{ sku: "SKU-LT-140", name: "Nimbus 14-inch laptop", quantity: 1, unit_price: "1299.00" }], total: "1299.00" }) });

    const primary = await screen.findByRole("button", { name: "Override and refund $1,299.00" });
    expect(screen.getByText(/Override needed\. \$1299\.00 is above the supervisor limit\./)).toBeInTheDocument();
    expect(primary).toBeDisabled();

    const reason = screen.getByLabelText(`Reason for the override (at least ${MIN_OVERRIDE_REASON} characters)`);
    await userEvent.type(reason, "too short");
    expect(primary).toBeDisabled();
    await userEvent.type(reason, " but now it is long enough");
    expect(primary).toBeEnabled();
    await userEvent.click(primary);
    expect(take).toHaveBeenCalledWith("t1", { action: "refund", skus: [], note: "too short but now it is long enough" });
  });

  it("explains what is never allowed and offers no way to do it", async () => {
    check.mockResolvedValue(actionCheck({ verdict: "blocked", rule: "remedy_already_applied", reason: "This order already had a refund; each order gets one." }));
    setup();
    expect(await screen.findByText(/Not possible\. This order already had a refund/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Refund $29.98" })).toBeDisabled();
  });

  it("shows the API's reason when an action fails", async () => {
    vi.spyOn(api, "takeStaffAction").mockRejectedValue(new Error("This ticket is already closed"));
    setup();
    await userEvent.click(await screen.findByRole("button", { name: "Refund $29.98" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("This ticket is already closed");
  });

  it("closes without an action as declined, with the note", async () => {
    const close = vi.spyOn(api, "closeTicket").mockResolvedValue(ticket({ status: "rejected" }));
    setup();
    await screen.findByRole("button", { name: "Refund $29.98" });
    await userEvent.type(screen.getByLabelText("Note for the audit log (optional)"), "Not a defect");
    await userEvent.click(screen.getByRole("button", { name: "Close without action" }));
    await userEvent.click(screen.getByLabelText("Declined"));
    await userEvent.click(screen.getByRole("button", { name: "Confirm close" }));
    expect(close).toHaveBeenCalledWith("t1", "declined", "Not a defect");
    expect(await screen.findByRole("status")).toHaveTextContent("Closed as declined.");
  });

  it("sends a reply to the customer", async () => {
    const reply = vi.spyOn(api, "staffReply").mockResolvedValue(message({ sender: "staff" }));
    setup();
    await userEvent.type(screen.getByLabelText("Message the customer"), "Could you send a photo?");
    await userEvent.click(screen.getByRole("button", { name: "Send reply" }));
    expect(reply).toHaveBeenCalledWith("t1", "Could you send a photo?");
    await waitFor(() => expect(screen.getByLabelText("Message the customer")).toHaveValue(""));
  });

  it("only offers a reply on a closed ticket", () => {
    setup({ mode: "reply", ticket: ticket({ status: "resolved", customer_replied: true }) });
    expect(screen.getByText(/The customer wrote after this ticket closed\./)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Refund/ })).not.toBeInTheDocument();
    expect(check).not.toHaveBeenCalled();
  });

  it("without an order, only replying and closing are possible", () => {
    setup({ order: null });
    expect(screen.getByText(/No order is linked to this ticket/)).toBeInTheDocument();
    expect(check).not.toHaveBeenCalled();
  });
});
