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

export interface TicketDetail {
  ticket: Ticket;
  messages: Message[];
  approvals: Approval[];
  trace: AuditEntry[];
  customer_context: CustomerContext | null;
  customer_context_error: string | null;
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
