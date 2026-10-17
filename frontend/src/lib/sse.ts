import type { AgentEvent, AgentEventType } from "@/lib/types";

/**
 * Server-Sent Events over fetch. EventSource cannot send an Authorization header, so the
 * stream is read from a fetch body and parsed here. Reconnects with backoff and resumes from
 * the last event id, so a dropped connection never loses events.
 */

const EVENT_TYPES = new Set<AgentEventType>([
  "step_started",
  "step_finished",
  "message",
  "approval_requested",
  "completed",
  "error",
]);

interface Frame {
  id: number | null;
  event: string;
  data: string;
}

/** Split buffered text into complete frames; anything after the last blank line is kept. */
export function parseFrames(buffer: string): { frames: Frame[]; rest: string } {
  const normalized = buffer.replace(/\r\n/g, "\n");
  const blocks = normalized.split("\n\n");
  const rest = blocks.pop() ?? "";
  const frames: Frame[] = [];
  for (const block of blocks) {
    let id: number | null = null;
    let event = "message";
    const data: string[] = [];
    for (const line of block.split("\n")) {
      if (!line || line.startsWith(":")) continue;
      const colon = line.indexOf(":");
      const field = colon === -1 ? line : line.slice(0, colon);
      const value = colon === -1 ? "" : line.slice(colon + 1).replace(/^ /, "");
      if (field === "id") id = Number.isFinite(Number(value)) ? Number(value) : null;
      else if (field === "event") event = value;
      else if (field === "data") data.push(value);
    }
    if (data.length) frames.push({ id, event, data: data.join("\n") });
  }
  return { frames, rest };
}

export function toAgentEvent(frame: Frame): AgentEvent | null {
  if (!EVENT_TYPES.has(frame.event as AgentEventType)) return null;
  try {
    return { id: frame.id, type: frame.event as AgentEventType, data: JSON.parse(frame.data) };
  } catch {
    return null;
  }
}

export interface StreamOptions {
  token: string;
  onEvent: (event: AgentEvent) => void;
  onStatus?: (connected: boolean) => void;
  /** Injected in tests; defaults to window.fetch. */
  fetchImpl?: typeof fetch;
  maxBackoffMs?: number;
}

/** Keep a stream open until the returned function is called. */
export function connectEvents(url: string, options: StreamOptions): () => void {
  const controller = new AbortController();
  const fetchImpl = options.fetchImpl ?? fetch;
  let lastEventId: number | null = null;
  let attempt = 0;

  const run = async (): Promise<void> => {
    while (!controller.signal.aborted) {
      try {
        const response = await fetchImpl(url, {
          headers: {
            Authorization: `Bearer ${options.token}`,
            Accept: "text/event-stream",
            ...(lastEventId === null ? {} : { "Last-Event-ID": String(lastEventId) }),
          },
          signal: controller.signal,
        });
        // Access errors will not fix themselves; stop instead of retrying forever.
        if (response.status === 401 || response.status === 403 || response.status === 404) {
          options.onStatus?.(false);
          return;
        }
        if (!response.ok || !response.body) throw new Error(`stream ${response.status}`);
        attempt = 0;
        options.onStatus?.(true);
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const parsed = parseFrames(buffer);
          buffer = parsed.rest;
          for (const frame of parsed.frames) {
            if (frame.id !== null) lastEventId = frame.id;
            const event = toAgentEvent(frame);
            if (event) options.onEvent(event);
          }
        }
      } catch {
        if (controller.signal.aborted) return;
      }
      options.onStatus?.(false);
      attempt += 1;
      const delay = Math.min(options.maxBackoffMs ?? 10_000, 500 * 2 ** Math.min(attempt, 5));
      await new Promise((resolve) => setTimeout(resolve, delay));
    }
  };

  void run();
  return () => controller.abort();
}
