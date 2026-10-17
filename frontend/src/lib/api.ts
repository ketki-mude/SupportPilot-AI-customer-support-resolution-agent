import type {
  Approval,
  AuditEntry,
  ChatResponse,
  Page,
  Ticket,
  TicketDetail,
  TicketStatus,
  TokenResponse,
  User,
} from "@/lib/types";

/** An error from the API's {"error": {"code", "message"}} envelope, or a network failure. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

interface ApiHooks {
  getToken: () => string | null;
  onUnauthorized: () => void;
}

let hooks: ApiHooks = { getToken: () => null, onUnauthorized: () => undefined };

/** Called once by the auth provider so every request carries the current token. */
export function configureApi(next: ApiHooks): void {
  hooks = next;
}

type Query = Record<string, string | number | undefined | null>;

function withQuery(path: string, query?: Query): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== null && value !== "") params.set(key, String(value));
  }
  const search = params.toString();
  return search ? `${path}?${search}` : path;
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const token = hooks.getToken();
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      method,
      headers: {
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "network_error", "Can't reach the server. Check your connection.");
  }
  if (response.status === 401 && token) hooks.onUnauthorized();
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as {
      error?: { code?: string; message?: string };
    } | null;
    throw new ApiError(
      response.status,
      payload?.error?.code ?? `http_${response.status}`,
      payload?.error?.message ?? response.statusText,
    );
  }
  return (await response.json()) as T;
}

export interface TicketQuery {
  status?: TicketStatus;
  q?: string;
  page?: number;
  page_size?: number;
}

export const api = {
  login: (email: string, password: string) =>
    request<TokenResponse>("POST", "/auth/login", { email, password }),
  me: () => request<User>("GET", "/auth/me"),

  createTicket: (message: string) => request<ChatResponse>("POST", "/chat/tickets", { message }),
  sendMessage: (ticketId: string, message: string) =>
    request<ChatResponse>("POST", `/chat/tickets/${ticketId}/messages`, { message }),

  listTickets: (query: TicketQuery = {}) =>
    request<Page<Ticket>>("GET", withQuery("/tickets", { ...query })),
  getTicket: (ticketId: string) => request<TicketDetail>("GET", `/tickets/${ticketId}`),

  listApprovals: (status: Approval["status"] = "pending") =>
    request<Page<Approval>>("GET", withQuery("/approvals", { status })),
  decide: (approvalId: string, decision: "approve" | "reject", note?: string) =>
    request<Approval>("POST", `/approvals/${approvalId}/decision`, {
      decision,
      note: note?.trim() ? note.trim() : null,
    }),

  listAudit: (query: { ticket_id?: string; page?: number; page_size?: number } = {}) =>
    request<Page<AuditEntry>>("GET", withQuery("/audit", query)),
};

export const streamUrl = (ticketId: string) => `/api/chat/tickets/${ticketId}/stream`;
