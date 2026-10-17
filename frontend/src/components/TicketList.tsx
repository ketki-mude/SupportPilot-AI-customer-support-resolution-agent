import { Link } from "react-router-dom";

import { StatusBadge } from "@/components/StatusBadge";
import { PRIORITY_LABEL, ticketTitle, timeAgo } from "@/lib/format";
import type { Ticket, TicketPriority } from "@/lib/types";
import { cn } from "@/lib/utils";

const DOT: Record<TicketPriority, string> = {
  high: "bg-danger",
  normal: "bg-accent",
  low: "bg-border",
};

interface TicketListProps {
  tickets: Ticket[];
  selectedId: string | null;
}

export function TicketList({ tickets, selectedId }: TicketListProps) {
  return (
    <ul className="flex flex-col" aria-label="Tickets">
      {tickets.map((ticket) => (
        <li key={ticket.id}>
          <Link
            to={`/console/${ticket.id}`}
            aria-current={ticket.id === selectedId ? "page" : undefined}
            className={cn(
              "flex flex-col gap-1 border-b-hairline border-border px-4 py-3 hover:bg-muted",
              ticket.id === selectedId && "bg-muted",
            )}
          >
            <div className="flex items-center gap-2">
              <span
                className={cn("h-2 w-2 shrink-0 rounded-full", DOT[ticket.priority])}
                title={PRIORITY_LABEL[ticket.priority]}
                aria-label={PRIORITY_LABEL[ticket.priority]}
              />
              <span className="truncate font-medium">{ticketTitle(ticket.summary, ticket.category)}</span>
            </div>
            <div className="flex items-center gap-2 pl-4 text-xs text-subtle">
              <span className="truncate">{ticket.customer_name ?? ticket.customer_id}</span>
              <span aria-hidden>·</span>
              <span className="shrink-0">{ticket.public_id}</span>
              <span className="ml-auto shrink-0">{timeAgo(ticket.updated_at)}</span>
            </div>
            <div className="flex items-center gap-2 pl-4">
              <StatusBadge status={ticket.status} />
              {ticket.customer_replied ? (
                <span className="text-xs text-warning">Customer replied</span>
              ) : null}
            </div>
          </Link>
        </li>
      ))}
    </ul>
  );
}
