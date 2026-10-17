import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, ApiError, configureApi } from "@/lib/api";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const UNAUTHORIZED = json(401, { error: { code: "invalid_token", message: "Invalid or expired token" } });

describe("api client sessions", () => {
  let token: string | null;
  const refresh = vi.fn<() => Promise<string | null>>();
  const onUnauthorized = vi.fn();
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    token = "old-token";
    refresh.mockReset();
    onUnauthorized.mockReset();
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
    configureApi({ getToken: () => token, refresh, onUnauthorized });
  });

  afterEach(() => vi.unstubAllGlobals());

  const authHeader = (call: number) =>
    (fetchMock.mock.calls[call]?.[1]?.headers as Record<string, string>).Authorization;

  it("renews an expired token once and retries the request", async () => {
    fetchMock.mockResolvedValueOnce(UNAUTHORIZED.clone()).mockResolvedValueOnce(json(200, { id: "u1" }));
    refresh.mockImplementation(async () => {
      token = "new-token";
      return token;
    });

    await expect(api.me()).resolves.toEqual({ id: "u1" });
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(authHeader(0)).toBe("Bearer old-token");
    expect(authHeader(1)).toBe("Bearer new-token");
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("shares one refresh between requests that fail together", async () => {
    fetchMock.mockImplementation(async (_url, init) =>
      (init?.headers as Record<string, string>).Authorization === "Bearer new-token"
        ? json(200, { items: [], total: 0, page: 1, page_size: 20 })
        : UNAUTHORIZED.clone(),
    );
    refresh.mockImplementation(async () => {
      await new Promise((resolve) => setTimeout(resolve, 10));
      token = "new-token";
      return token;
    });

    await Promise.all([api.listTickets(), api.listTickets(), api.listApprovals()]);
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("signs out when the session cannot be renewed", async () => {
    fetchMock.mockResolvedValue(UNAUTHORIZED.clone());
    refresh.mockResolvedValue(null);

    await expect(api.me()).rejects.toMatchObject({ status: 401, code: "invalid_token" });
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });

  it("never tries to refresh around the session endpoints themselves", async () => {
    fetchMock.mockResolvedValue(
      json(401, { error: { code: "invalid_credentials", message: "Incorrect email or password" } }),
    );
    await expect(api.login("a@b.co", "wrong")).rejects.toBeInstanceOf(ApiError);
    await expect(api.refresh()).rejects.toBeInstanceOf(ApiError);
    expect(refresh).not.toHaveBeenCalled();
  });

  it("sends the refresh cookie and handles empty responses", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    await expect(api.logout()).resolves.toBeUndefined();
    expect(fetchMock.mock.calls[0]?.[1]?.credentials).toBe("same-origin");
  });
});
