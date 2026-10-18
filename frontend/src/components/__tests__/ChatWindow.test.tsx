import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ChatWindow } from "@/components/ChatWindow";
import type { StepState } from "@/hooks/useAgentStream";
import type { Message, TicketStatus } from "@/lib/types";
import { message } from "@/test/fixtures";

const CONVERSATION: Message[] = [
  message({ id: "m1", sender: "customer", content: "My headphones arrived damaged" }),
  message({ id: "m2", sender: "agent", content: "I've asked a supervisor to approve a refund of $79.99." }),
];

function setup(
  props: Partial<{
    messages: Message[];
    status: TicketStatus | null;
    steps: StepState[];
    running: boolean;
    sending: boolean;
    closed: boolean;
    onSend: (text: string) => Promise<unknown>;
  }> = {},
) {
  const onSend = props.onSend ?? vi.fn().mockResolvedValue(undefined);
  const onStartNew = vi.fn();
  const view = render(
    <ChatWindow
      header={<header>Acme support</header>}
      messages={props.messages ?? CONVERSATION}
      status={props.status === undefined ? "awaiting_approval" : props.status}
      steps={props.steps ?? []}
      running={props.running ?? false}
      sending={props.sending ?? false}
      onSend={onSend}
      closed={props.closed ?? false}
      onStartNew={onStartNew}
    />,
  );
  return { onSend, onStartNew, ...view };
}

describe("ChatWindow", () => {
  it("puts the customer on the right in the accent color and the assistant on the left", () => {
    setup();
    const customer = screen.getByText("My headphones arrived damaged");
    const agent = screen.getByText(/asked a supervisor/);
    expect(customer).toHaveClass("bg-accent");
    expect(customer.parentElement).toHaveClass("items-end");
    expect(agent).toHaveClass("bg-muted");
    expect(agent.parentElement).toHaveClass("items-start");
  });

  it("shows the status badge under the latest assistant message", () => {
    setup();
    expect(screen.getByTestId("latest-status")).toHaveTextContent("Waiting for approval");
  });

  it("hides the status while the agent is working and shows its live steps instead", () => {
    setup({
      running: true,
      steps: [
        { step: "intake", status: "done" },
        { step: "customer_data", status: "running" },
      ],
    });
    expect(screen.queryByTestId("latest-status")).not.toBeInTheDocument();
    const progress = screen.getByRole("list", { name: "Agent progress" });
    expect(progress).toHaveTextContent("Reading your message");
    expect(progress).toHaveTextContent("Looking up your order");
  });

  it("sends on Enter, keeps Shift+Enter for new lines, and clears the box", async () => {
    const { onSend } = setup();
    const box = screen.getByLabelText("Message");
    await userEvent.type(box, "Here is a photo{Shift>}{Enter}{/Shift}of the damage{Enter}");
    expect(onSend).toHaveBeenCalledWith("Here is a photo\nof the damage");
    expect(box).toHaveValue("");
  });

  it("keeps the draft if sending fails", async () => {
    const { onSend } = setup({ onSend: vi.fn().mockRejectedValue(new Error("offline")) });
    const box = screen.getByLabelText("Message");
    await userEvent.type(box, "Hello?");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(onSend).toHaveBeenCalled();
    expect(box).toHaveValue("Hello?");
  });

  it("has one filled button and disables it when there is nothing to send", async () => {
    const { container } = setup();
    expect(container.querySelectorAll('[data-variant="primary"]')).toHaveLength(1);
    const send = screen.getByRole("button", { name: "Send" });
    expect(send).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Message"), "   ");
    expect(send).toBeDisabled();
  });

  it("shows no status badge on an open ticket", () => {
    setup({ status: "open" });
    expect(screen.queryByTestId("latest-status")).not.toBeInTheDocument();
  });

  it("offers a new request, not the message box, once a request is closed", async () => {
    const { onStartNew, container } = setup({ status: "resolved", closed: true });
    expect(screen.queryByLabelText("Message")).not.toBeInTheDocument();
    expect(screen.getByText("This request is closed. The assistant won't reply to it again.")).toBeInTheDocument();
    const primary = container.querySelectorAll('[data-variant="primary"]');
    expect(primary).toHaveLength(1);
    expect(primary[0]).toHaveTextContent("Start a new request");
    await userEvent.click(screen.getByRole("button", { name: "Start a new request" }));
    expect(onStartNew).toHaveBeenCalledTimes(1);
  });

  it("still lets the customer add a note to a closed request", async () => {
    const { onSend } = setup({ status: "resolved", closed: true });
    await userEvent.click(screen.getByRole("button", { name: "Add a note to this request" }));
    expect(screen.getByText("Your note goes to our support staff. It won't reopen this request.")).toBeInTheDocument();
    const box = screen.getByLabelText("Message");
    expect(box).toHaveFocus();
    await userEvent.type(box, "Thanks, all sorted{Enter}");
    expect(onSend).toHaveBeenCalledWith("Thanks, all sorted");
  });

  it("labels messages from a person on the support team", () => {
    setup({
      messages: [...CONVERSATION, message({ id: "m3", sender: "staff", content: "Could you send a photo?" })],
      status: "escalated",
    });
    expect(screen.getByText("Support team")).toBeInTheDocument();
    expect(screen.queryByText("Acme assistant")).not.toBeInTheDocument();
  });
});
