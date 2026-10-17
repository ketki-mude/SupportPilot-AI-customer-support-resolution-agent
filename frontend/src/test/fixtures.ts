import type { Approval, AuditEntry, Message } from "@/lib/types";

export function message(overrides: Partial<Message> = {}): Message {
  return {
    id: crypto.randomUUID(),
    ticket_id: "t1",
    sender: "customer",
    content: "My headphones arrived damaged, I want a refund",
    created_at: new Date().toISOString(),
    ...overrides,
  };
}

export function approval(overrides: Partial<Approval> = {}): Approval {
  return {
    id: "a1",
    ticket_id: "t1",
    status: "pending",
    decided_by: null,
    note: null,
    decided_at: null,
    created_at: new Date().toISOString(),
    proposed_action: {
      action: "refund",
      amount: "79.99",
      skus: ["SKU-HP-450"],
      order_id: "ORD-10001",
      rule: "needs_supervisor",
      policy_reason: "$79.99 needs a supervisor's approval.",
      agent_reason: "Item arrived damaged within the refund window.",
      confidence: 0.92,
      citations: [{ source: "refund_policy.md", section: "3.2 Damaged or defective items" }],
    },
    ...overrides,
  };
}

export function auditEntry(overrides: Partial<AuditEntry> = {}): AuditEntry {
  return {
    id: crypto.randomUUID(),
    actor: "agent",
    actor_label: "agent",
    action: "policy_decision",
    tool_name: null,
    input: {},
    output: { rule: "needs_supervisor" },
    status: "needs_approval",
    created_at: "2026-10-17T10:15:00Z",
    ticket_public_id: "TCK-1001",
    ...overrides,
  };
}
