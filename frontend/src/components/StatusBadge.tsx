import { Badge, type BadgeTone } from "@/components/ui/badge";
import { STATUS_LABEL } from "@/lib/format";
import type { TicketStatus } from "@/lib/types";

const TONE: Record<TicketStatus, BadgeTone> = {
  open: "accent",
  awaiting_approval: "warning",
  resolved: "success",
  escalated: "danger",
  rejected: "neutral",
};

export function StatusBadge({ status }: { status: TicketStatus }) {
  return <Badge tone={TONE[status]}>{STATUS_LABEL[status]}</Badge>;
}

const RESULT_TONE: Record<string, BadgeTone> = {
  success: "success",
  approved: "success",
  auto_approve: "success",
  pending: "warning",
  needs_approval: "warning",
  flagged: "warning",
  failure: "danger",
  deny: "danger",
  rejected: "danger",
  escalate: "danger",
  escalated: "danger",
};

/** Audit results: green for done or allowed, amber for waiting or flagged, red for refused. */
export function ResultBadge({ result }: { result: string }) {
  return <Badge tone={RESULT_TONE[result] ?? "neutral"}>{result.replace(/_/g, " ")}</Badge>;
}
