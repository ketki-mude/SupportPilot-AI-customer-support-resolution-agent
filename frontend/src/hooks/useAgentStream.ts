import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { ticketKeys } from "@/hooks/useTickets";
import { streamUrl } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { connectEvents } from "@/lib/sse";
import type { AgentEvent } from "@/lib/types";

export interface StepState {
  step: string;
  status: "running" | "done" | "failed";
}

export interface AgentStreamState {
  connected: boolean;
  /** True while the agent is working on the latest run. */
  running: boolean;
  steps: StepState[];
}

const INITIAL: AgentStreamState = { connected: false, running: false, steps: [] };

/** Fold one event into the live view of the current run. */
export function reduceAgentEvent(state: AgentStreamState, event: AgentEvent): AgentStreamState {
  const step = typeof event.data.step === "string" ? event.data.step : null;
  switch (event.type) {
    case "step_started":
      if (!step || step === "await_approval") return { ...state, running: false };
      // A new intake means a new run: start a fresh step list.
      return {
        ...state,
        running: true,
        steps:
          step === "intake"
            ? [{ step, status: "running" }]
            : [...state.steps.filter((s) => s.step !== step), { step, status: "running" }],
      };
    case "step_finished":
      return {
        ...state,
        steps: state.steps.map((s) =>
          s.step === step ? { ...s, status: event.data.status === "failed" ? "failed" : "done" } : s,
        ),
      };
    case "approval_requested":
    case "completed":
    case "error":
      return { ...state, running: false };
    default:
      return state;
  }
}

/** Live agent progress for a ticket; refreshes the ticket whenever the agent says something. */
export function useAgentStream(ticketId: string | null): AgentStreamState {
  const { token } = useAuth();
  const queryClient = useQueryClient();
  const [state, setState] = useState<AgentStreamState>(INITIAL);

  useEffect(() => {
    setState(INITIAL);
    if (!ticketId || !token) return;
    return connectEvents(streamUrl(ticketId), {
      token,
      onStatus: (connected) => setState((current) => ({ ...current, connected })),
      onEvent: (event) => {
        setState((current) => reduceAgentEvent(current, event));
        if (event.type !== "step_started" && event.type !== "step_finished") {
          void queryClient.invalidateQueries({ queryKey: ticketKeys.detail(ticketId) });
          void queryClient.invalidateQueries({ queryKey: ticketKeys.all });
        }
      },
    });
  }, [ticketId, token, queryClient]);

  return state;
}
