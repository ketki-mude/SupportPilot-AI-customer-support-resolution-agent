import { LogOut, Plus } from "lucide-react";
import { type ReactNode, useState } from "react";

import { AcmeMark } from "@/components/AcmeMark";
import { ChatWindow } from "@/components/ChatWindow";
import { StateMessage } from "@/components/StateMessage";
import { Button } from "@/components/ui/button";
import { useAgentStream } from "@/hooks/useAgentStream";
import { useCreateTicket, useSendMessage, useTicket, useTicketList } from "@/hooks/useTickets";
import { useAuth } from "@/lib/auth";
import type { TicketStatus } from "@/lib/types";

// Escalated tickets stay open to the customer: a specialist reads and answers them here.
const CLOSED: TicketStatus[] = ["resolved", "rejected"];

export default function Chat() {
  const { logout } = useAuth();
  const latest = useTicketList({ page_size: 1 });
  const [createdId, setCreatedId] = useState<string | null>(null);
  const [startFresh, setStartFresh] = useState(false);

  const activeId = createdId ?? (startFresh ? null : (latest.data?.items[0]?.id ?? null));
  const detail = useTicket(activeId);
  const stream = useAgentStream(activeId);
  const create = useCreateTicket();
  const send = useSendMessage(activeId ?? "none");

  const ticket = detail.data?.ticket ?? null;
  const sendError = create.error ?? send.error;

  const onSend = async (text: string) => {
    if (activeId) return send.mutateAsync(text);
    const response = await create.mutateAsync(text);
    setCreatedId(response.ticket.id);
    setStartFresh(false);
    return response;
  };

  const startNewRequest = () => {
    setCreatedId(null);
    setStartFresh(true);
  };

  const header = (
    <header className="flex h-16 shrink-0 items-center gap-3 border-b-hairline border-border bg-surface px-4">
      <AcmeMark className="h-9 w-9" />
      <div className="min-w-0">
        <p className="font-semibold leading-tight">Acme support</p>
        <p className="text-xs text-subtle">
          Replies in seconds{ticket ? ` · ${ticket.public_id}` : ""}
        </p>
      </div>
      <div className="ml-auto flex items-center gap-1">
        {activeId ? (
          <Button variant="ghost" size="sm" onClick={startNewRequest}>
            <Plus className="h-4 w-4" aria-hidden />
            New request
          </Button>
        ) : null}
        <Button variant="ghost" size="icon" aria-label="Sign out" onClick={logout}>
          <LogOut className="h-4 w-4" aria-hidden />
        </Button>
      </div>
    </header>
  );

  if (latest.isPending || (activeId && detail.isPending)) {
    return (
      <Shell>
        {header}
        <StateMessage kind="loading" title="Loading your conversation" className="flex-1" />
      </Shell>
    );
  }
  if (latest.isError || detail.isError) {
    return (
      <Shell>
        {header}
        <StateMessage
          kind="error"
          title="We couldn't load your conversation"
          detail="Check your connection and try again."
          onRetry={() => void (latest.isError ? latest.refetch() : detail.refetch())}
          className="flex-1"
        />
      </Shell>
    );
  }

  return (
    <Shell>
      <ChatWindow
        // A different ticket gets a fresh composer (and a fresh "add a note" choice).
        key={activeId ?? "new"}
        header={header}
        messages={detail.data?.messages ?? []}
        status={ticket?.status ?? null}
        steps={stream.steps}
        running={stream.running}
        onSend={onSend}
        sending={create.isPending || send.isPending}
        error={sendError ? sendError.message : null}
        closed={ticket !== null && CLOSED.includes(ticket.status)}
        notice={
          ticket?.status === "escalated"
            ? "A specialist is handling this request and will reply here."
            : null
        }
        onStartNew={startNewRequest}
        emptyState={
          <div className="flex flex-col items-center gap-1 py-16 text-center">
            <p className="text-base font-semibold">How can we help today?</p>
            <p className="max-w-xs text-subtle">
              Tell us what happened, for example that an item arrived damaged or an order is late.
            </p>
          </div>
        }
      />
    </Shell>
  );
}

function Shell({ children }: { children: ReactNode }) {
  return <main className="mx-auto flex h-[100dvh] max-w-3xl flex-col bg-background sm:border-x-hairline sm:border-border">{children}</main>;
}
