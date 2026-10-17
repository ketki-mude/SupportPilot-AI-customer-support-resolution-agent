import { Sparkles } from "lucide-react";

import { StatusBadge } from "@/components/StatusBadge";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { agentUsage, CATEGORY_LABEL } from "@/lib/format";
import type { Ticket, TicketUsage } from "@/lib/types";

/** Two or three lines from the intake step, so staff know the case before reading it. */
export function AiSummary({ ticket, usage }: { ticket: Ticket; usage?: TicketUsage | null }) {
  return (
    <Card>
      <CardContent className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2 text-xs text-subtle">
          <Sparkles className="h-3.5 w-3.5 text-accent" aria-hidden />
          <span>AI summary</span>
          <span aria-hidden>·</span>
          <span>{ticket.public_id}</span>
          {ticket.category ? <Badge>{CATEGORY_LABEL[ticket.category]}</Badge> : null}
          {ticket.order_id ? <Badge>{ticket.order_id}</Badge> : null}
          <span className="ml-auto">
            <StatusBadge status={ticket.status} />
          </span>
        </div>
        <p className="line-clamp-3 leading-relaxed">
          {ticket.summary ?? "The agent hasn't summarized this request yet."}
        </p>
        {usage && usage.runs > 0 ? (
          <p className="text-xs text-subtle">{agentUsage(usage)}</p>
        ) : null}
      </CardContent>
    </Card>
  );
}
