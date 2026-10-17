import type { TicketCategory, TicketPriority, TicketStatus } from "@/lib/types";

export function money(amount: string | number | null | undefined): string {
  if (amount === null || amount === undefined || amount === "") return "";
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(
    Number(amount),
  );
}

export function shortDate(iso: string | null | undefined): string {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

export function dateTime(iso: string): string {
  return new Date(iso).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function timeAgo(iso: string, now: Date = new Date()): string {
  const seconds = Math.max(0, Math.round((now.getTime() - new Date(iso).getTime()) / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  return shortDate(iso);
}

export function percent(confidence: number): string {
  return `${Math.round(confidence * 100)}%`;
}

export const STATUS_LABEL: Record<TicketStatus, string> = {
  open: "Open",
  awaiting_approval: "Waiting for approval",
  resolved: "Resolved",
  escalated: "With a specialist",
  rejected: "Closed",
};

export const CATEGORY_LABEL: Record<TicketCategory, string> = {
  refund: "Refund",
  replacement: "Replacement",
  missing_order: "Missing order",
  cancellation: "Cancellation",
  billing: "Billing",
  other: "Other request",
};

export const PRIORITY_LABEL: Record<TicketPriority, string> = {
  low: "Low priority",
  normal: "Normal priority",
  high: "High priority",
};

const ACTION_LABEL: Record<string, string> = {
  refund: "Refund",
  replacement: "Replacement",
  cancel: "Cancel order",
  info: "Answer",
  escalate: "Escalate",
};

export function actionLabel(action: string): string {
  return ACTION_LABEL[action] ?? sentence(action);
}

/** What the customer sees while the agent works on each step. */
const STEP_ACTIVITY: Record<string, string | null> = {
  intake: "Reading your message",
  customer_data: "Looking up your order",
  policy_rag: "Checking our policies",
  resolution: "Working out the best fix",
  policy_gate: "Checking the rules",
  request_approval: "Asking a supervisor",
  await_approval: null,
  executor: "Processing your request",
  notifier: "Writing a reply",
  clarify: "Writing a reply",
  escalation: "Passing this to a specialist",
};

export function stepActivity(step: string): string | null {
  return step in STEP_ACTIVITY ? (STEP_ACTIVITY[step] ?? null) : sentence(step);
}

const STEP_NAME: Record<string, string> = {
  intake: "Intake",
  customer_data: "Customer data",
  policy_rag: "Policy search",
  resolution: "Resolution",
  policy_gate: "Policy gate",
  request_approval: "Request approval",
  await_approval: "Await approval",
  executor: "Execute action",
  notifier: "Notify customer",
  clarify: "Clarify order",
  escalation: "Escalation",
};

/** Staff-facing names for agent steps, from "step:policy_rag" or "policy_rag". */
export function stepName(step: string): string {
  const key = step.replace(/^step:/, "");
  return STEP_NAME[key] ?? sentence(key);
}

/** "policy_decision" -> "Policy decision". Sentence case everywhere. */
export function sentence(value: string): string {
  const text = value.replace(/[_:]+/g, " ").trim();
  return text ? text[0]!.toUpperCase() + text.slice(1).toLowerCase() : text;
}

/** The first sentence of the AI summary, used as a ticket's title. */
export function ticketTitle(summary: string | null, category: TicketCategory | null): string {
  const first = summary?.split(/(?<=[.!?])\s/)[0]?.trim();
  return first || (category ? CATEGORY_LABEL[category] : "New request");
}
