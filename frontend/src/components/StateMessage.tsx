import { AlertCircle, Inbox, Loader2 } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface StateMessageProps {
  kind: "loading" | "empty" | "error";
  title: string;
  detail?: ReactNode;
  onRetry?: () => void;
  className?: string;
}

/** The loading, empty, and error states every page uses. */
export function StateMessage({ kind, title, detail, onRetry, className }: StateMessageProps) {
  const Icon = kind === "loading" ? Loader2 : kind === "error" ? AlertCircle : Inbox;
  return (
    <div
      role={kind === "error" ? "alert" : "status"}
      className={cn("flex flex-col items-center gap-2 px-6 py-10 text-center", className)}
    >
      <Icon
        aria-hidden
        className={cn(
          "h-5 w-5",
          kind === "loading" && "animate-spin text-subtle",
          kind === "error" && "text-danger",
          kind === "empty" && "text-subtle",
        )}
      />
      <p className="font-medium">{title}</p>
      {detail ? <p className="max-w-sm text-subtle">{detail}</p> : null}
      {onRetry ? (
        <Button variant="ghost" size="sm" onClick={onRetry}>
          Try again
        </Button>
      ) : null}
    </div>
  );
}
