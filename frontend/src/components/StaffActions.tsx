import { AlertTriangle, CheckCircle2, XCircle } from "lucide-react";
import { type FormEvent, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useActionCheck, useCloseTicket, useStaffAction, useStaffReply } from "@/hooks/useStaff";
import { ApiError } from "@/lib/api";
import { money } from "@/lib/format";
import type { ActionCheck, Order, StaffActionKind, Ticket } from "@/lib/types";
import { cn } from "@/lib/utils";

/** An override reason must say something an auditor can use. Mirrors the API's minimum. */
export const MIN_OVERRIDE_REASON = 10;

const ACTIONS: { value: StaffActionKind; label: string }[] = [
  { value: "refund", label: "Refund" },
  { value: "replacement", label: "Replacement" },
  { value: "cancel", label: "Cancel order" },
];

interface StaffActionsProps {
  ticket: Ticket;
  order: Order | null;
  /** handle: the agent handed the ticket over. reply: the ticket is closed; staff can still write. */
  mode: "handle" | "reply";
  /** The agent is working on the ticket right now. */
  busy?: boolean;
}

/** People take over where the agent stops: reply, act within (or knowingly outside) the rules, or close. */
export function StaffActions({ ticket, order, mode, busy = false }: StaffActionsProps) {
  const [done, setDone] = useState<string | null>(null);
  return (
    <section aria-label="Handle this ticket" className="flex flex-col gap-4 rounded-card border-hairline border-border bg-surface p-4">
      {mode === "handle" ? (
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
          <h2 className="font-semibold">Handle this ticket</h2>
          <p className="text-xs text-subtle">The agent handed this over. Whatever you do is recorded under your name.</p>
        </div>
      ) : null}
      {done ? (
        <p role="status" className="flex items-center gap-2 text-success">
          <CheckCircle2 className="h-4 w-4 shrink-0" aria-hidden />
          {done}
        </p>
      ) : null}
      <ReplyForm ticket={ticket} closed={mode === "reply"} />
      {mode === "handle" ? <ActionForm ticket={ticket} order={order} busy={busy} onDone={setDone} /> : null}
    </section>
  );
}

function errorText(error: unknown): string {
  return error instanceof ApiError || error instanceof Error ? error.message : "Something went wrong";
}

function ReplyForm({ ticket, closed }: { ticket: Ticket; closed: boolean }) {
  const [text, setText] = useState("");
  const reply = useStaffReply(ticket.id);
  const sending = useRef(false);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const message = text.trim();
    if (!message || sending.current) return;
    sending.current = true;
    reply.mutate(message, {
      onSuccess: () => setText(""),
      onSettled: () => {
        sending.current = false;
      },
    });
  };

  return (
    <form onSubmit={submit} className="flex flex-col gap-2">
      <label htmlFor={`reply-${ticket.id}`} className="text-xs font-medium text-subtle">
        Message the customer
      </label>
      {closed ? (
        <p className="text-xs text-subtle">
          {ticket.customer_replied ? "The customer wrote after this ticket closed. " : ""}
          Your message goes to their chat; it doesn't reopen the ticket.
        </p>
      ) : null}
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
        <Textarea
          id={`reply-${ticket.id}`}
          rows={2}
          maxLength={2000}
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder="Write to the customer"
        />
        <Button type="submit" variant="outline" disabled={!text.trim() || reply.isPending}>
          {reply.isPending ? "Sending…" : "Send reply"}
        </Button>
      </div>
      {reply.isError ? (
        <p role="alert" className="text-xs text-danger">
          {errorText(reply.error)}
        </p>
      ) : null}
    </form>
  );
}

function primaryLabel(check: ActionCheck | undefined, action: StaffActionKind): string {
  const amount = check?.amount ? ` ${money(check.amount)}` : "";
  const what = action === "refund" ? `refund${amount}` : action === "replacement" ? "send a replacement" : "cancel the order";
  const sentence = check?.verdict === "override" ? `Override and ${what}` : what;
  return sentence.charAt(0).toUpperCase() + sentence.slice(1);
}

function CheckLine({ check }: { check: ActionCheck }) {
  if (check.verdict === "allowed") {
    return (
      <p className="flex items-start gap-2 text-success">
        <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
        <span>Within the rules. {check.reason}</span>
      </p>
    );
  }
  if (check.verdict === "override") {
    return (
      <p className="flex items-start gap-2 text-warning">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
        <span>
          Override needed. {check.reason} Write why below; it goes in the audit log.
        </span>
      </p>
    );
  }
  return (
    <p className="flex items-start gap-2 text-danger">
      <XCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
      <span>Not possible. {check.reason}</span>
    </p>
  );
}

function ActionForm({
  ticket,
  order,
  busy,
  onDone,
}: {
  ticket: Ticket;
  order: Order | null;
  busy: boolean;
  onDone: (text: string) => void;
}) {
  const [action, setAction] = useState<StaffActionKind>("refund");
  const [unchecked, setUnchecked] = useState<string[]>([]);
  const [note, setNote] = useState("");
  const [closing, setClosing] = useState(false);
  const [outcome, setOutcome] = useState<"resolved" | "declined">("resolved");
  const take = useStaffAction(ticket.id);
  const close = useCloseTicket(ticket.id);
  // A fast double click fires twice before React re-renders; only the first one counts.
  const submitting = useRef(false);

  const items = order?.items ?? [];
  const itemized = action !== "cancel" && items.length > 1;
  const chosen = items.filter((item) => !unchecked.includes(item.sku)).map((item) => item.sku);
  // All items selected is the whole order: send no SKUs.
  const skus = itemized && chosen.length < items.length ? chosen : [];
  const nothingChosen = itemized && chosen.length === 0;
  const check = useActionCheck(ticket.id, { action, skus }, Boolean(order) && !nothingChosen);

  const verdict = check.data?.verdict;
  const reasonMissing = verdict === "override" && note.trim().length < MIN_OVERRIDE_REASON;
  const canAct = Boolean(order) && !nothingChosen && verdict !== undefined && verdict !== "blocked" && !reasonMissing && !busy && !take.isPending;

  const submit = () => {
    if (!canAct || submitting.current) return;
    submitting.current = true;
    take.mutate(
      { action, skus, note: note.trim() || null },
      {
        // A successful action is final for this ticket; only a failure frees the button again.
        onError: () => {
          submitting.current = false;
        },
        onSuccess: (result) => {
          const amount = result.amount ? ` ${money(result.amount)}` : "";
          const what = action === "refund" ? `Refunded${amount}` : action === "replacement" ? "Replacement sent" : "Order cancelled";
          onDone(`${what}. The customer has been told.`);
        },
      },
    );
  };

  const confirmClose = () => {
    if (submitting.current) return;
    submitting.current = true;
    close.mutate(
      { outcome, note: note.trim() || null },
      {
        onError: () => {
          submitting.current = false;
        },
        onSuccess: () => onDone(outcome === "declined" ? "Closed as declined. The customer has been told." : "Closed. The customer has been told."),
      },
    );
  };

  return (
    <fieldset className="flex flex-col gap-3 border-t-hairline border-border pt-4" disabled={busy}>
      <legend className="sr-only">Resolve with an action</legend>
      <p className="text-xs font-medium text-subtle">Resolve with an action</p>
      <div className="flex flex-wrap gap-1" role="group" aria-label="Action">
        {ACTIONS.map((option) => (
          <Button
            key={option.value}
            variant="ghost"
            size="sm"
            aria-pressed={action === option.value}
            className={cn(action === option.value && "bg-muted text-foreground")}
            onClick={() => setAction(option.value)}
          >
            {option.label}
          </Button>
        ))}
      </div>

      {itemized ? (
        <ul className="flex flex-col gap-1" aria-label="Items">
          {items.map((item) => (
            <li key={item.sku}>
              <label className="flex cursor-pointer items-center gap-2">
                <input
                  type="checkbox"
                  checked={!unchecked.includes(item.sku)}
                  onChange={(event) =>
                    setUnchecked((current) =>
                      event.target.checked ? current.filter((sku) => sku !== item.sku) : [...current, item.sku],
                    )
                  }
                />
                <span>{item.name}</span>
                <span className="text-subtle">{money(item.unit_price)}</span>
              </label>
            </li>
          ))}
        </ul>
      ) : null}

      {!order ? (
        <p className="text-subtle">No order is linked to this ticket, so only a reply or closing is possible.</p>
      ) : nothingChosen ? (
        <p className="text-subtle">Choose at least one item.</p>
      ) : check.isError ? (
        <p role="alert" className="text-danger">
          {errorText(check.error)}
        </p>
      ) : check.data ? (
        <CheckLine check={check.data} />
      ) : (
        <p className="text-subtle">Checking the rules…</p>
      )}

      <label className="flex flex-col gap-1">
        <span className="text-xs font-medium text-subtle">
          {verdict === "override" ? `Reason for the override (at least ${MIN_OVERRIDE_REASON} characters)` : "Note for the audit log (optional)"}
        </span>
        <Textarea rows={2} maxLength={1000} value={note} onChange={(event) => setNote(event.target.value)} />
      </label>

      {take.isError ? (
        <p role="alert" className="text-danger">
          {errorText(take.error)}
        </p>
      ) : null}
      {close.isError ? (
        <p role="alert" className="text-danger">
          {errorText(close.error)}
        </p>
      ) : null}

      {closing ? (
        <div className="flex flex-col gap-2 rounded-control bg-muted/60 p-3">
          <p className="text-xs font-medium text-subtle">Close without an action, as</p>
          <div className="flex flex-wrap gap-3" role="radiogroup" aria-label="Outcome">
            {(["resolved", "declined"] as const).map((value) => (
              <label key={value} className="flex cursor-pointer items-center gap-2">
                <input type="radio" name={`outcome-${ticket.id}`} checked={outcome === value} onChange={() => setOutcome(value)} />
                {value === "resolved" ? "Resolved, nothing more to do" : "Declined"}
              </label>
            ))}
          </div>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={confirmClose} disabled={close.isPending}>
              {close.isPending ? "Closing…" : "Confirm close"}
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setClosing(false)}>
              Cancel
            </Button>
          </div>
        </div>
      ) : null}

      <div className="flex flex-wrap items-center justify-between gap-2">
        <Button variant="ghost" onClick={() => setClosing(true)} disabled={closing}>
          Close without action
        </Button>
        <Button variant="primary" onClick={submit} disabled={!canAct}>
          {take.isPending ? "Working…" : primaryLabel(check.data, action)}
        </Button>
      </div>
    </fieldset>
  );
}
