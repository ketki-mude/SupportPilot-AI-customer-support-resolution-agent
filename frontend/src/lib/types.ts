/** API shapes, mirroring the backend's Pydantic schemas. Money arrives as decimal strings. */

export type Role = "customer" | "agent" | "supervisor" | "admin";

export interface User {
  id: string;
  email: string;
  role: Role;
  customer_id: string | null;
}

export interface TokenResponse {
  access_token: string;
  token_type: "bearer";
  expires_in: number;
  user: User;
}

export type TicketStatus = "open" | "awaiting_approval" | "resolved" | "escalated" | "rejected";
export type TicketCategory =
  | "refund"
  | "replacement"
  | "missing_order"
  | "cancellation"
  | "billing"
  | "other";
export type TicketPriority = "low" | "normal" | "high";

export interface Ticket {
  id: string;
  public_id: string;
  customer_id: string;
  order_id: string | null;
  category: TicketCategory | null;
  status: TicketStatus;
  priority: TicketPriority;
  summary: string | null;
  customer_replied: boolean;
  created_at: string;
  updated_at: string;
  customer_name?: string | null;
}

export type Sender = "customer" | "agent" | "supervisor" | "system";

export interface Message {
  id: string;
  ticket_id: string;
  sender: Sender;
  content: string;
  created_at: string;
}

export interface Citation {
  source: string;
  section: string;
}

export interface ProposedAction {
  action: "refund" | "replacement" | "cancel" | "info" | "escalate";
  amount: string | null;
  skus: string[];
  order_id: string;
  rule: string;
  policy_reason: string;
  agent_reason: string;
  confidence: number;
  citations: Citation[];
}

export type ApprovalStatus = "pending" | "approved" | "rejected";

export interface Approval {
  id: string;
  ticket_id: string;
  proposed_action: ProposedAction;
  status: ApprovalStatus;
  decided_by: string | null;
  note: string | null;
  decided_at: string | null;
  created_at: string;
  ticket_public_id?: string;
}

export interface AuditEntry {
  id: string;
  actor: string;
  action: string;
  tool_name: string | null;
  input: Record<string, unknown>;
  output: Record<string, unknown>;
  status: string;
  created_at: string;
  ticket_public_id?: string | null;
  actor_label?: string;
}

export interface OrderItem {
  sku: string;
  name: string;
  quantity: number;
  unit_price: string;
}

export interface Order {
  order_id: string;
  customer_id: string;
  status: "processing" | "shipped" | "in_transit" | "delivered" | "cancelled";
  payment_status: "authorized" | "captured" | "voided";
  items: OrderItem[];
  total: string;
  currency: "USD";
  placed_at: string;
  shipped_at: string | null;
  delivered_at: string | null;
  estimated_delivery_at: string | null;
  cancelled_at: string | null;
  tracking_number: string | null;
  payment_method_last4: string;
  refunded_amount: string;
  remedy: "refund" | "replacement" | null;
}

export interface CustomerContext {
  customer: {
    id: string;
    name: string;
    tier: "standard" | "gold" | "platinum";
    fraud_flag: boolean;
    member_since: string;
  };
  order: Order | null;
  recent_orders: {
    order_id: string;
    status: Order["status"];
    total: string;
    placed_at: string;
    items: string[];
  }[];
}

export interface TicketUsage {
  runs: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: string;
  latency_ms: number;
  last_run_status: string | null;
}

export interface TicketDetail {
  ticket: Ticket;
  messages: Message[];
  approvals: Approval[];
  trace: AuditEntry[];
  customer_context: CustomerContext | null;
  customer_context_error: string | null;
  usage: TicketUsage | null;
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface ChatResponse {
  ticket: Ticket;
  messages: Message[];
}

export type AgentEventType =
  | "step_started"
  | "step_finished"
  | "message"
  | "approval_requested"
  | "completed"
  | "error";

export interface AgentEvent {
  id: number | null;
  type: AgentEventType;
  data: Record<string, unknown>;
}

export type EvalLlm = "gemini" | "mock";

export interface EvalCategoryScore {
  cases: number;
  passed: number;
}

export interface EvalMetrics {
  cases: number;
  passed: number;
  policy_compliant: number;
  unauthorized: number;
  tool_calls: number;
  tool_successes: number;
  total_latency_ms: number;
  total_cost_usd: string;
  total_input_tokens: number;
  total_output_tokens: number;
  by_category: Record<string, EvalCategoryScore>;
  correct_resolution_rate: number;
  policy_compliance_rate: number;
  unauthorized_rate: number;
  tool_success_rate: number;
  avg_latency_ms: number;
  avg_cost_usd: string;
}

export interface EvalRunSummary {
  id: string;
  started_at: string;
  duration_seconds: number;
  llm_mode: "live" | "mock";
  provider: string;
  model: string;
  temperature: number;
  git_commit: string;
  dataset: { path: string; sha256: string; cases: number };
  limit: number | null;
  cases_planned: number;
  cases_scored: number;
  complete: boolean;
  stopped_reason: string | null;
  estimated_cost_usd: string;
  metrics: EvalMetrics;
}

export interface EvalCaseResult {
  id: string;
  category: string;
  passed: boolean;
  policy_compliant: boolean;
  unauthorized: boolean;
  expected_intent: string | null;
  observed_intent: string | null;
  expected_action: string;
  observed_action: string;
  expected_requires_approval: boolean;
  observed_requires_approval: boolean;
  rule: string | null;
  reasons: string[];
  tool_calls: number;
  tool_successes: number;
  latency_ms: number;
  cost_usd: string;
}

export interface EvalReport extends EvalRunSummary {
  results: EvalCaseResult[];
}

export interface EvalJob {
  id: string;
  status: "running" | "finished" | "failed";
  llm: EvalLlm;
  limit: number | null;
  cases_planned: number;
  cases_done: number;
  started_at: string;
  finished_at: string | null;
  report_id: string | null;
  error: string | null;
}

export interface EvalOverview {
  current: EvalJob | null;
  runs: EvalRunSummary[];
  dataset_cases: number;
  model: string;
  full_run_estimate_usd: string;
}
