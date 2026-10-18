import { ArrowUp, Plus } from "lucide-react";
import { type FormEvent, type KeyboardEvent, type ReactNode, useEffect, useRef, useState } from "react";

import { AgentTrace } from "@/components/AgentTrace";
import { MessageBubble } from "@/components/MessageBubble";
import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import type { StepState } from "@/hooks/useAgentStream";
import type { Message, TicketStatus } from "@/lib/types";

const MAX_LENGTH = 2000;

interface ChatWindowProps {
  messages: Message[];
  status: TicketStatus | null;
  steps: StepState[];
  running: boolean;
  onSend: (text: string) => Promise<unknown>;
  sending: boolean;
  error?: string | null;
  header: ReactNode;
  emptyState?: ReactNode;
  /** A line above the composer. */
  notice?: ReactNode;
  /**
   * The request is resolved, rejected, or with a specialist. The agent will not run on it again,
   * so the main action is a new request; a note to staff stays possible behind a link.
   */
  closed?: boolean;
  onStartNew?: () => void;
}

/** Messenger-style conversation: customer on the right, the assistant on the left. */
export function ChatWindow({
  messages,
  status,
  steps,
  running,
  onSend,
  sending,
  error,
  header,
  emptyState,
  notice,
  closed = false,
  onStartNew,
}: ChatWindowProps) {
  const [draft, setDraft] = useState("");
  const [addingNote, setAddingNote] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);
  const lastAgentIndex = messages.findLastIndex((m) => m.sender !== "customer");
  const showStatus = status !== null && status !== "open";

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: "end" });
  }, [messages.length, running, steps.length]);

  const submit = async (event?: FormEvent) => {
    event?.preventDefault();
    const text = draft.trim();
    if (!text || sending) return;
    setDraft("");
    try {
      await onSend(text);
    } catch {
      setDraft(text); // keep what they typed so they can retry
    }
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) void submit(event);
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      {header}
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-5" aria-label="Conversation">
        <div className="mx-auto flex max-w-2xl flex-col gap-4">
          {messages.length === 0 ? emptyState : null}
          {messages.map((message, index) => (
            <div key={message.id} className="flex flex-col gap-1.5">
              <MessageBubble message={message} perspective="customer" />
              {index === lastAgentIndex && showStatus && !running ? (
                <div className="px-1" data-testid="latest-status">
                  <StatusBadge status={status} />
                </div>
              ) : null}
            </div>
          ))}
          {running ? <AgentTrace steps={steps} className="px-1" /> : null}
          <div ref={endRef} />
        </div>
      </div>
      {closed && !addingNote ? (
        <div className="border-t-hairline border-border bg-surface px-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] pt-3">
          <div className="mx-auto flex max-w-2xl flex-col items-center gap-2 py-1 text-center">
            <p className="text-subtle">This request is closed. The assistant won't reply to it again.</p>
            <Button variant="primary" onClick={onStartNew}>
              <Plus className="h-4 w-4" aria-hidden />
              Start a new request
            </Button>
            <button
              type="button"
              onClick={() => setAddingNote(true)}
              className="text-xs text-subtle underline-offset-2 hover:text-foreground hover:underline"
            >
              Add a note to this request
            </button>
          </div>
        </div>
      ) : (
        <form
          onSubmit={submit}
          className="border-t-hairline border-border bg-surface px-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] pt-3"
        >
          {closed ? (
            <p className="mx-auto mb-2 max-w-2xl text-xs text-subtle">
              Your note goes to our support staff. It won't reopen this request.
            </p>
          ) : null}
          {notice ? <p className="mx-auto mb-2 max-w-2xl text-xs text-subtle">{notice}</p> : null}
          {error ? (
            <p role="alert" className="mx-auto mb-2 max-w-2xl text-xs text-danger">
              {error}
            </p>
          ) : null}
          <div className="mx-auto flex max-w-2xl items-end gap-2">
            <label htmlFor="chat-input" className="sr-only">
              Message
            </label>
            <Textarea
              id="chat-input"
              rows={1}
              maxLength={MAX_LENGTH}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={onKeyDown}
              placeholder={closed ? "Write a note" : "Write a message"}
              className="max-h-36 min-h-[2.5rem]"
              autoFocus={addingNote || messages.length === 0}
            />
            <Button
              type="submit"
              variant="primary"
              size="icon"
              aria-label="Send"
              disabled={!draft.trim() || sending}
              className="h-10 w-10 shrink-0"
            >
              <ArrowUp className="h-4 w-4" aria-hidden />
            </Button>
          </div>
        </form>
      )}
    </div>
  );
}
