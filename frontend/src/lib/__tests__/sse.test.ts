import { describe, expect, it, vi } from "vitest";

import { connectEvents, parseFrames, toAgentEvent } from "@/lib/sse";
import type { AgentEvent } from "@/lib/types";

describe("parseFrames", () => {
  it("parses complete frames and keeps the partial remainder", () => {
    const { frames, rest } = parseFrames(
      'retry: 3000\n\nid: 1\nevent: step_started\ndata: {"step":"intake"}\n\n: keep-alive\n\nid: 2\nevent: mess',
    );
    expect(frames).toEqual([{ id: 1, event: "step_started", data: '{"step":"intake"}' }]);
    expect(rest).toBe("id: 2\nevent: mess");
  });

  it("handles CRLF line endings and multi-line data", () => {
    const { frames } = parseFrames("event: message\r\ndata: line one\r\ndata: line two\r\n\r\n");
    expect(frames[0]).toEqual({ id: null, event: "message", data: "line one\nline two" });
  });

  it("ignores unknown events and malformed JSON", () => {
    expect(toAgentEvent({ id: 1, event: "surprise", data: "{}" })).toBeNull();
    expect(toAgentEvent({ id: 1, event: "message", data: "not json" })).toBeNull();
  });
});

function streamResponse(chunks: string[], status = 200): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  return new Response(body, { status, headers: { "Content-Type": "text/event-stream" } });
}

describe("connectEvents", () => {
  it("delivers events split across chunks and resumes from the last id", async () => {
    const events: AgentEvent[] = [];
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(
        streamResponse(['id: 7\nevent: step_started\nda', 'ta: {"step":"intake"}\n\n']),
      )
      .mockImplementation(() => new Promise(() => undefined)); // second connection stays open
    const stop = connectEvents("/stream", {
      token: "tok",
      fetchImpl,
      maxBackoffMs: 1,
      onEvent: (event) => events.push(event),
    });
    await vi.waitFor(() => expect(fetchImpl).toHaveBeenCalledTimes(2));
    stop();

    expect(events).toEqual([{ id: 7, type: "step_started", data: { step: "intake" } }]);
    const [, firstInit] = fetchImpl.mock.calls[0] as [string, RequestInit];
    const [, secondInit] = fetchImpl.mock.calls[1] as [string, RequestInit];
    expect(firstInit.headers).toMatchObject({ Authorization: "Bearer tok" });
    expect(secondInit.headers).toMatchObject({ "Last-Event-ID": "7" });
  });

  it("stops without retrying when access is denied", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(streamResponse([], 404));
    const onStatus = vi.fn();
    connectEvents("/stream", { token: "tok", fetchImpl, maxBackoffMs: 1, onEvent: vi.fn(), onStatus });
    await vi.waitFor(() => expect(onStatus).toHaveBeenCalledWith(false));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });
});
