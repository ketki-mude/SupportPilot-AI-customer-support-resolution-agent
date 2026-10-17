import { describe, expect, it } from "vitest";

import { type AgentStreamState, reduceAgentEvent } from "@/hooks/useAgentStream";
import type { AgentEvent, AgentEventType } from "@/lib/types";

const start: AgentStreamState = { connected: true, running: false, steps: [] };
const event = (type: AgentEventType, data: Record<string, unknown> = {}): AgentEvent => ({ id: 1, type, data });

function replay(...events: AgentEvent[]): AgentStreamState {
  return events.reduce(reduceAgentEvent, start);
}

describe("reduceAgentEvent", () => {
  it("tracks the running step of the current run", () => {
    const state = replay(
      event("step_started", { step: "intake" }),
      event("step_finished", { step: "intake", status: "ok" }),
      event("step_started", { step: "customer_data" }),
    );
    expect(state.running).toBe(true);
    expect(state.steps).toEqual([
      { step: "intake", status: "done" },
      { step: "customer_data", status: "running" },
    ]);
  });

  it("stops running when the agent pauses for approval or finishes", () => {
    expect(replay(event("step_started", { step: "intake" }), event("approval_requested")).running).toBe(false);
    expect(replay(event("step_started", { step: "intake" }), event("completed")).running).toBe(false);
    expect(replay(event("step_started", { step: "await_approval" })).running).toBe(false);
  });

  it("starts a fresh step list when a new run begins", () => {
    const state = replay(
      event("step_started", { step: "intake" }),
      event("step_started", { step: "notifier" }),
      event("completed"),
      event("step_started", { step: "intake" }),
    );
    expect(state.steps).toEqual([{ step: "intake", status: "running" }]);
  });

  it("marks failed steps", () => {
    const state = replay(
      event("step_started", { step: "executor" }),
      event("step_finished", { step: "executor", status: "failed" }),
    );
    expect(state.steps).toEqual([{ step: "executor", status: "failed" }]);
  });
});
