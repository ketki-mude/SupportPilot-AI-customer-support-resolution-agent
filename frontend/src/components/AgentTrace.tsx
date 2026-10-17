import { Check, Loader2, X } from "lucide-react";

import type { StepState } from "@/hooks/useAgentStream";
import { stepActivity } from "@/lib/format";
import { cn } from "@/lib/utils";

/** What the agent is doing right now, one line per step, updated live over SSE. */
export function AgentTrace({ steps, className }: { steps: StepState[]; className?: string }) {
  const visible = steps.filter((s) => stepActivity(s.step));
  if (!visible.length) return null;
  return (
    <ol aria-label="Agent progress" aria-live="polite" className={cn("flex flex-col gap-1.5", className)}>
      {visible.map(({ step, status }) => (
        <li key={step} className="flex items-center gap-2 text-xs text-subtle">
          {status === "running" ? (
            <Loader2 aria-hidden className="h-3.5 w-3.5 animate-spin" />
          ) : status === "failed" ? (
            <X aria-hidden className="h-3.5 w-3.5 text-danger" />
          ) : (
            <Check aria-hidden className="h-3.5 w-3.5 text-success" />
          )}
          <span className={cn(status === "running" && "text-foreground")}>{stepActivity(step)}</span>
        </li>
      ))}
    </ol>
  );
}
