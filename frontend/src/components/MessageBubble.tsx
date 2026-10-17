import { timeAgo } from "@/lib/format";
import type { Message } from "@/lib/types";
import { cn } from "@/lib/utils";

interface MessageBubbleProps {
  message: Message;
  /** Whose side of the conversation is "mine": the customer in chat, the team in the console. */
  perspective: "customer" | "staff";
}

const SENDER_LABEL: Record<Message["sender"], string> = {
  customer: "Customer",
  agent: "Acme assistant",
  supervisor: "Supervisor",
  system: "System",
};

export function MessageBubble({ message, perspective }: MessageBubbleProps) {
  const fromCustomer = message.sender === "customer";
  const mine = perspective === "customer" ? fromCustomer : !fromCustomer;
  const pending = message.id.startsWith("pending-");
  return (
    <div className={cn("flex flex-col gap-1", mine ? "items-end" : "items-start")}>
      {perspective === "staff" ? (
        <span className="px-1 text-xs text-subtle">{SENDER_LABEL[message.sender]}</span>
      ) : null}
      <div
        data-sender={message.sender}
        className={cn(
          "max-w-[85%] whitespace-pre-line break-words rounded-card px-3.5 py-2.5 leading-relaxed sm:max-w-[75%]",
          mine ? "bg-accent text-accent-foreground" : "bg-muted text-foreground",
          pending && "opacity-70",
        )}
      >
        {message.content}
      </div>
      <span className="px-1 text-xs text-subtle">
        {pending ? "Sending…" : timeAgo(message.created_at)}
      </span>
    </div>
  );
}
