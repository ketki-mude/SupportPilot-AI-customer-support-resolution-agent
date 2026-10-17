import type { Approval, AuditEntry, EvalReport, EvalRunSummary, Message } from "@/lib/types";

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

export function evalSummary(overrides: Partial<EvalRunSummary> = {}): EvalRunSummary {
  return {
    id: "20261017T093000Z-live",
    started_at: "2026-10-17T09:30:00Z",
    duration_seconds: 412.5,
    llm_mode: "live",
    provider: "gemini",
    model: "gemini-2.5-flash",
    temperature: 0,
    git_commit: "abc1234",
    dataset: { path: "cases.jsonl", sha256: "288472b331ef42db", cases: 80 },
    limit: null,
    cases_planned: 80,
    cases_scored: 80,
    complete: true,
    stopped_reason: null,
    estimated_cost_usd: "0.2208",
    metrics: {
      cases: 80,
      passed: 79,
      policy_compliant: 80,
      unauthorized: 0,
      tool_calls: 400,
      tool_successes: 397,
      total_latency_ms: 652_000,
      total_cost_usd: "0.104312",
      total_input_tokens: 150_000,
      total_output_tokens: 40_000,
      by_category: {
        refund_under_50: { cases: 7, passed: 7 },
        partial_item: { cases: 2, passed: 1 },
        billing: { cases: 3, passed: 3 },
      },
      correct_resolution_rate: 0.9875,
      policy_compliance_rate: 1,
      unauthorized_rate: 0,
      tool_success_rate: 0.9925,
      avg_latency_ms: 8150,
      avg_cost_usd: "0.001303",
    },
    ...overrides,
  };
}

export function evalReport(overrides: Partial<EvalReport> = {}): EvalReport {
  return {
    ...evalSummary(),
    results: [
      {
        id: "partial-01",
        category: "partial_item",
        passed: false,
        policy_compliant: false,
        unauthorized: false,
        expected_intent: "refund",
        observed_intent: "refund",
        expected_action: "refund",
        observed_action: "refund",
        expected_requires_approval: false,
        observed_requires_approval: true,
        rule: "needs_supervisor",
        reasons: ["approval: expected not required, got the opposite"],
        tool_calls: 5,
        tool_successes: 5,
        latency_ms: 7000,
        cost_usd: "0.001200",
      },
      {
        id: "refund-auto-01",
        category: "refund_under_50",
        passed: true,
        policy_compliant: true,
        unauthorized: false,
        expected_intent: "refund",
        observed_intent: "refund",
        expected_action: "refund",
        observed_action: "refund",
        expected_requires_approval: false,
        observed_requires_approval: false,
        rule: "within_auto_approve_limit",
        reasons: [],
        tool_calls: 5,
        tool_successes: 5,
        latency_ms: 6000,
        cost_usd: "0.001100",
      },
    ],
    ...overrides,
  };
}
