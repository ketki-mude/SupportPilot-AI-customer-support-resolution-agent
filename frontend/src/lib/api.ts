import type {
  Approval,
  AuditEntry,
  ActionCheck,
  ChatResponse,
  EvalJob,
  EvalLlm,
  EvalOverview,
  EvalReport,
  Message,
  Page,
  StaffActionInput,
  StaffActionResult,
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
  /** Exchange the refresh cookie for a new access token, or null if the session is over. */
  refresh: () => Promise<string | null>;
  onUnauthorized: () => void;
}

let hooks: ApiHooks = {
  getToken: () => null,
  refresh: async () => null,
  onUnauthorized: () => undefined,
};
let refreshing: Promise<string | null> | null = null;

/** Called by the auth provider so every request carries the current token. */
export function configureApi(next: ApiHooks): void {
  hooks = next;
}

/** One refresh at a time: requests that fail together all wait for the same new token. */
function refreshOnce(): Promise<string | null> {
  refreshing ??= hooks.refresh().finally(() => {
    refreshing = null;
  });
  return refreshing;
}

const SESSION_PATHS = new Set(["/auth/login", "/auth/refresh", "/auth/logout"]);

type Query = Record<string, string | number | undefined | null>;

function withQuery(path: string, query?: Query): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== null && value !== "") params.set(key, String(value));
  }
  const search = params.toString();
  return search ? `${path}?${search}` : path;
}

async function send(method: string, path: string, body: unknown, token: string | null): Promise<Response> {
  try {
    return await fetch(`/api${path}`, {
      method,
      credentials: "same-origin",
      headers: {
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "network_error", "Can't reach the server. Check your connection.");
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const token = hooks.getToken();
  let response = await send(method, path, body, token);
  // An expired access token: renew it once with the refresh cookie, then retry.
  if (response.status === 401 && !SESSION_PATHS.has(path)) {
    const fresh = await refreshOnce();
    if (fresh) response = await send(method, path, body, fresh);
    if (response.status === 401 && token) hooks.onUnauthorized();
  }
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
  if (response.status === 204) return undefined as T;
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
  refresh: () => request<TokenResponse>("POST", "/auth/refresh"),
  logout: () => request<void>("POST", "/auth/logout"),
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

  staffReply: (ticketId: string, message: string) =>
    request<Message>("POST", `/tickets/${ticketId}/reply`, { message }),
  checkStaffAction: (ticketId: string, input: StaffActionInput) =>
    request<ActionCheck>("POST", `/tickets/${ticketId}/actions/check`, input),
  takeStaffAction: (ticketId: string, input: StaffActionInput & { note: string | null }) =>
    request<StaffActionResult>("POST", `/tickets/${ticketId}/actions`, input),
  closeTicket: (ticketId: string, outcome: "resolved" | "declined", note: string | null) =>
    request<Ticket>("POST", `/tickets/${ticketId}/close`, { outcome, note }),

  listAudit: (query: { ticket_id?: string; page?: number; page_size?: number } = {}) =>
    request<Page<AuditEntry>>("GET", withQuery("/audit", query)),

  evalOverview: () => request<EvalOverview>("GET", "/evals/runs"),
  evalReport: (reportId: string) => request<EvalReport>("GET", `/evals/runs/${reportId}`),
  startEval: (llm: EvalLlm, limit: number | null) =>
    request<EvalJob>("POST", "/evals/run", { llm, limit }),
};

export const streamUrl = (ticketId: string) => `/api/chat/tickets/${ticketId}/stream`;
