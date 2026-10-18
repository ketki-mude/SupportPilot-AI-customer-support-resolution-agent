import { ArrowLeft } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { AgentTrace } from "@/components/AgentTrace";
import { AiSummary } from "@/components/AiSummary";
import { ApprovalBar, type DecisionResult } from "@/components/ApprovalBar";
import { StaffActions } from "@/components/StaffActions";
import { CustomerPanel } from "@/components/CustomerPanel";
import { MessageBubble } from "@/components/MessageBubble";
import { QuickJump, useQuickJumpShortcut } from "@/components/QuickJump";
import { StaffShell } from "@/components/StaffShell";
import { StateMessage } from "@/components/StateMessage";
import { ResultBadge } from "@/components/StatusBadge";
import { TicketList } from "@/components/TicketList";
import { Button } from "@/components/ui/button";
import { useAgentStream } from "@/hooks/useAgentStream";
import { useDecision } from "@/hooks/useApprovals";
import { useTicket, useTicketList } from "@/hooks/useTickets";
import { DECIDER_ROLES, useAuth } from "@/lib/auth";
import { dateTime, sentence, stepName } from "@/lib/format";
import type { AuditEntry, TicketDetail, TicketStatus } from "@/lib/types";
import { cn } from "@/lib/utils";

type Filter = "all" | "approval" | "escalated";

const FILTERS: Record<Filter, { label: string; empty: string; detail?: string }> = {
  all: { label: "All tickets", empty: "No tickets yet" },
  approval: {
    label: "Needs approval",
    empty: "Nothing needs approval",
    detail: "New approvals appear here automatically.",
  },
  escalated: {
    label: "With a specialist",
    empty: "Nothing is waiting for a specialist",
    detail: "Tickets the agent hands over appear here.",
  },
};
const FILTER_ORDER: Filter[] = ["all", "approval", "escalated"];
const STATUS_FOR: Record<Filter, TicketStatus | undefined> = {
  all: undefined,
  approval: "awaiting_approval",
  escalated: "escalated",
};
const HANDLED_BY_STAFF: TicketStatus[] = ["escalated", "open"];

export default function Console() {
  const { ticketId = null } = useParams();
  const [filter, setFilter] = useState<Filter>("all");
  const [searchOpen, setSearchOpen] = useState(false);
  const openSearch = useCallback(() => setSearchOpen(true), []);
  useQuickJumpShortcut(openSearch);

  const list = useTicketList({ status: STATUS_FOR[filter], page_size: 50 }, { refetchInterval: 5_000 });
  const current = FILTERS[filter];

  return (
    <StaffShell onOpenSearch={openSearch}>
      <QuickJump open={searchOpen} onOpenChange={setSearchOpen} />
      <div className="grid h-full min-h-0 grid-cols-1 md:grid-cols-[300px_1fr] xl:grid-cols-[320px_1fr_300px]">
        <aside
          className={cn(
            "min-h-0 flex-col border-r-hairline border-border bg-surface md:flex",
            ticketId ? "hidden" : "flex",
          )}
          aria-label="Ticket queue"
        >
          <div className="flex flex-wrap gap-1 border-b-hairline border-border p-2" role="group" aria-label="Filter">
            {FILTER_ORDER.map((value) => (
              <Button
                key={value}
                variant="ghost"
                size="sm"
                aria-pressed={filter === value}
                className={cn(filter === value && "bg-muted text-foreground")}
                onClick={() => setFilter(value)}
              >
                {FILTERS[value].label}
              </Button>
            ))}
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto">
            {list.isPending ? (
              <StateMessage kind="loading" title="Loading tickets" />
            ) : list.isError ? (
              <StateMessage kind="error" title="Tickets didn't load" onRetry={() => void list.refetch()} />
            ) : list.data.items.length === 0 ? (
              <StateMessage kind="empty" title={current.empty} detail={current.detail} />
            ) : (
              <TicketList tickets={list.data.items} selectedId={ticketId} />
            )}
          </div>
        </aside>
        {ticketId ? (
          <TicketWorkspace key={ticketId} ticketId={ticketId} />
        ) : (
          <section className="hidden min-h-0 md:block xl:col-span-2">
            <StateMessage kind="empty" title="Select a ticket" detail="Or press ⌘K to jump to one." className="h-full justify-center" />
          </section>
        )}
      </div>
    </StaffShell>
  );
}

function TicketWorkspace({ ticketId }: { ticketId: string }) {
  const { user } = useAuth();
  const detail = useTicket(ticketId);
  const stream = useAgentStream(ticketId);
  const decision = useDecision(ticketId);
  const [result, setResult] = useState<DecisionResult | null>(null);
  const [decidedId, setDecidedId] = useState<string | null>(null);
  // A fast double click fires twice before React re-renders; only the first one counts.
  const submitting = useRef(false);

  useEffect(() => {
    if (result?.kind !== "error") return;
    const timer = setTimeout(() => setResult(null), 8_000);
    return () => clearTimeout(timer);
  }, [result]);

  if (detail.isPending) {
    return <StateMessage kind="loading" title="Loading ticket" className="xl:col-span-2" />;
  }
  if (detail.isError) {
    return (
      <StateMessage
        kind="error"
        title="This ticket didn't load"
        detail={detail.error.message}
        onRetry={() => void detail.refetch()}
        className="xl:col-span-2"
      />
    );
  }

  const data: TicketDetail = detail.data;
  const approval =
    data.approvals.find((a) => a.status === "pending") ??
    data.approvals.find((a) => a.id === decidedId) ??
    null;
  const canDecide = Boolean(user && DECIDER_ROLES.includes(user.role));

  const onDecide = (choice: "approve" | "reject", note: string) => {
    if (!approval || submitting.current) return;
    submitting.current = true;
    setDecidedId(approval.id);
    setResult(null);
    decision.mutate(
      { approvalId: approval.id, decision: choice, note },
      {
        onSuccess: (decided) => setResult({ kind: decided.status === "approved" ? "approved" : "rejected" }),
        onError: (error) => setResult({ kind: "error", message: error.message }),
        onSettled: () => {
          submitting.current = false;
        },
      },
    );
  };

  return (
    <>
      <section className="flex min-h-0 flex-col" aria-label="Ticket">
        <div className="flex flex-col gap-3 border-b-hairline border-border p-4">
          <Link to="/console" className="flex items-center gap-1 text-xs text-subtle md:hidden">
            <ArrowLeft className="h-3.5 w-3.5" aria-hidden /> All tickets
          </Link>
          <AiSummary ticket={data.ticket} usage={data.usage} />
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          <div className="mx-auto flex max-w-2xl flex-col gap-4">
            {data.messages.map((message) => (
              <MessageBubble key={message.id} message={message} perspective="staff" />
            ))}
            {stream.running ? <AgentTrace steps={stream.steps} /> : null}
            <TraceDetails trace={data.trace} />
          </div>
        </div>
        {approval ? (
          <div className="border-t-hairline border-border p-4">
            <ApprovalBar
              approval={approval}
              canDecide={canDecide}
              deciding={decision.isPending}
              result={result}
              onDecide={onDecide}
            />
          </div>
        ) : (
          <div className="max-h-[55%] overflow-y-auto border-t-hairline border-border p-4">
            <StaffActions
              ticket={data.ticket}
              order={data.customer_context?.order ?? null}
              mode={HANDLED_BY_STAFF.includes(data.ticket.status) ? "handle" : "reply"}
              busy={stream.running}
            />
          </div>
        )}
      </section>
      <aside className="hidden min-h-0 overflow-y-auto border-l-hairline border-border bg-surface xl:block" aria-label="Customer">
        <CustomerPanel context={data.customer_context} error={data.customer_context_error} />
      </aside>
    </>
  );
}

const TRACE_ACTIONS = new Set(["policy_decision", "approval_requested", "approval_decision", "escalated", "injection_suspected", "action_blocked", "run_failed", "staff_action", "staff_close"]);

/** The key agent decisions for this ticket, from the audit trail. */
function TraceDetails({ trace }: { trace: AuditEntry[] }) {
  const entries = trace.filter((e) => TRACE_ACTIONS.has(e.action) || e.action.startsWith("step:"));
  if (!entries.length) return null;
  return (
    <details className="rounded-card border-hairline border-border bg-surface">
      <summary className="cursor-pointer px-4 py-2.5 text-xs font-medium text-subtle">
        Agent steps and decisions ({entries.length})
      </summary>
      <ol className="flex flex-col px-4 pb-3">
        {entries.map((entry) => (
          <li key={entry.id} className="flex items-center gap-3 border-t-hairline border-border py-2 text-xs">
            <span className="w-24 shrink-0 tabular-nums text-subtle">{dateTime(entry.created_at)}</span>
            <span className="min-w-0 flex-1 truncate">
              {entry.action.startsWith("step:") ? stepName(entry.action) : sentence(entry.action)}
              {typeof entry.output.rule === "string" ? ` · ${entry.output.rule.replace(/_/g, " ")}` : ""}
            </span>
            <ResultBadge result={entry.status} />
          </li>
        ))}
      </ol>
    </details>
  );
}
