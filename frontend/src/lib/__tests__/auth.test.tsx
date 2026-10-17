import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider, useAuth } from "@/lib/auth";
import type { TokenResponse } from "@/lib/types";

const FIFTEEN_MINUTES = 15 * 60;

function session(token: string): TokenResponse {
  return {
    access_token: token,
    token_type: "bearer",
    expires_in: FIFTEEN_MINUTES,
    user: { id: "u1", email: "sam@acme.com", role: "agent", customer_id: null },
  };
}

function Probe() {
  const { status, token } = useAuth();
  return (
    <p>
      {status}:{token ?? "none"}
    </p>
  );
}

describe("AuthProvider sessions", () => {
  const fetchMock = vi.fn<typeof fetch>();
  let issued = 0;

  beforeEach(() => {
    vi.useFakeTimers();
    issued = 0;
    fetchMock.mockReset();
    fetchMock.mockImplementation(async () => {
      issued += 1;
      return new Response(JSON.stringify(session(`token-${issued}`)), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  async function boot() {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <AuthProvider>
          <Probe />
        </AuthProvider>
      </QueryClientProvider>,
    );
    await act(() => vi.advanceTimersByTimeAsync(0));
  }

  const refreshCalls = () =>
    fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/api/auth/refresh")).length;

  it("restores the session from the refresh cookie on load", async () => {
    await boot();
    expect(screen.getByText("authenticated:token-1")).toBeInTheDocument();
    const [, init] = fetchMock.mock.calls[0] ?? [];
    expect(init?.method).toBe("POST");
    expect(init?.credentials).toBe("same-origin");
  });

  it("renews the 15-minute token silently before it expires, again and again", async () => {
    await boot();
    // 80% of the lifetime: 12 minutes. Nothing happens a second earlier.
    await act(() => vi.advanceTimersByTimeAsync(12 * 60_000 - 1_000));
    expect(refreshCalls()).toBe(1);
    expect(screen.getByText("authenticated:token-1")).toBeInTheDocument();

    await act(() => vi.advanceTimersByTimeAsync(1_000));
    expect(refreshCalls()).toBe(2);
    expect(screen.getByText("authenticated:token-2")).toBeInTheDocument();

    await act(() => vi.advanceTimersByTimeAsync(12 * 60_000));
    expect(refreshCalls()).toBe(3);
    expect(screen.getByText("authenticated:token-3")).toBeInTheDocument();
  });

  it("stays signed out when there is no session to restore", async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ error: { code: "invalid_refresh_token", message: "No" } }), {
        status: 401,
        headers: { "Content-Type": "application/json" },
      }),
    );
    await boot();
    expect(screen.getByText("anonymous:none")).toBeInTheDocument();
    await act(() => vi.advanceTimersByTimeAsync(30 * 60_000));
    expect(refreshCalls()).toBe(1);
  });
});
