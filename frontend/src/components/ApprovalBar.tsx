import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { actionLabel, money, percent } from "@/lib/format";
import type { Approval } from "@/lib/types";

export type DecisionResult =
  | { kind: "approved" }
  | { kind: "rejected" }
  | { kind: "error"; message: string };

interface ApprovalBarProps {
  approval: Approval;
  canDecide: boolean;
  deciding: boolean;
  result: DecisionResult | null;
  onDecide: (decision: "approve" | "reject", note: string) => void;
}

const RESULT_TEXT: Record<"approved" | "rejected", string> = {
  approved: "Approved. The agent is carrying it out and will tell the customer.",
  rejected: "Rejected. The customer has been told the request wasn't approved.",
};

/** The pending action, why the agent proposed it, and the supervisor's decision. */
export function ApprovalBar({ approval, canDecide, deciding, result, onDecide }: ApprovalBarProps) {
  const [note, setNote] = useState("");
  const action = approval.proposed_action;
  const decided = approval.status !== "pending";
  const grounded = action.citations.length > 0;

  return (
    <section
      aria-label="Approval"
      className="flex flex-col gap-3 rounded-card border-hairline border-warning/50 bg-warning-soft/40 p-4"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <p className="text-base font-semibold">
          {actionLabel(action.action)} {money(action.amount)}
          <span className="ml-2 text-sm font-normal text-subtle">{action.order_id}</span>
        </p>
        <p className="text-xs text-subtle">
          {grounded ? "Policy match" : "No policy citation"}, agent {percent(action.confidence)} sure
        </p>
      </div>
      <p className="text-subtle">{action.agent_reason}</p>
      {grounded ? (
        <ul className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-subtle">
          {action.citations.map((c) => (
            <li key={`${c.source}-${c.section}`}>
              {c.source.replace(/_policy\.md$/, "")} {c.section}
            </li>
          ))}
        </ul>
      ) : null}

      {result?.kind === "error" ? (
        <p role="alert" className="text-danger">
          {result.message}
        </p>
      ) : null}
      {decided && result?.kind !== "error" ? (
        <p role="status" className={approval.status === "approved" ? "text-success" : "text-subtle"}>
          {RESULT_TEXT[approval.status === "approved" ? "approved" : "rejected"]}
        </p>
      ) : null}

      {!decided && canDecide ? (
        <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
          <label className="flex-1">
            <span className="sr-only">Note for the record (optional)</span>
            <Textarea
              rows={1}
              maxLength={1000}
              value={note}
              onChange={(event) => setNote(event.target.value)}
              placeholder="Add a note (optional)"
              disabled={deciding}
            />
          </label>
          <div className="flex gap-2">
            <Button variant="outline" disabled={deciding} onClick={() => onDecide("reject", note)}>
              Reject
            </Button>
            <Button variant="primary" disabled={deciding} onClick={() => onDecide("approve", note)}>
              {deciding ? "Saving…" : "Approve"}
            </Button>
          </div>
        </div>
      ) : null}
      {!decided && !canDecide ? (
        <p className="text-xs text-subtle">Waiting for a supervisor to decide.</p>
      ) : null}
    </section>
  );
}
