import { describe, expect, it } from "vitest";

import { money, sentence, stepActivity, stepName, ticketTitle, timeAgo } from "@/lib/format";

describe("format", () => {
  it("formats money from decimal strings", () => {
    expect(money("79.99")).toBe("$79.99");
    expect(money("1299")).toBe("$1,299.00");
    expect(money(null)).toBe("");
  });

  it("uses sentence case", () => {
    expect(sentence("policy_decision")).toBe("Policy decision");
    expect(sentence("step:customer_data")).toBe("Step customer data");
  });

  it("titles tickets from the first sentence of the summary", () => {
    expect(ticketTitle("Headphones arrived damaged. Wants a refund.", "refund")).toBe(
      "Headphones arrived damaged.",
    );
    expect(ticketTitle(null, "cancellation")).toBe("Cancellation");
    expect(ticketTitle(null, null)).toBe("New request");
  });

  it("describes agent steps for customers and hides the approval wait", () => {
    expect(stepActivity("customer_data")).toBe("Looking up your order");
    expect(stepActivity("await_approval")).toBeNull();
  });

  it("names agent steps for staff", () => {
    expect(stepName("step:policy_rag")).toBe("Policy search");
    expect(stepName("customer_data")).toBe("Customer data");
    expect(stepName("step:something_new")).toBe("Something new");
  });

  it("shows relative times", () => {
    const now = new Date("2026-10-17T12:00:00Z");
    expect(timeAgo("2026-10-17T11:59:30Z", now)).toBe("just now");
    expect(timeAgo("2026-10-17T11:45:00Z", now)).toBe("15 min ago");
    expect(timeAgo("2026-10-17T09:00:00Z", now)).toBe("3 h ago");
  });
});
