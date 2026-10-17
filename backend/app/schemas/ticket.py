"""Ticket and message representations."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import MessageSender, TicketCategory, TicketPriority, TicketStatus
from app.schemas.approval import ApprovalRead
from app.schemas.commerce import Order, OrderStatus


class TicketUpdate(BaseModel):
    """Fields that may change on a ticket. Omitted fields stay as they are."""

    model_config = ConfigDict(extra="forbid")

    status: TicketStatus | None = None
    category: TicketCategory | None = None
    priority: TicketPriority | None = None
    summary: str | None = Field(default=None, max_length=2000)
    order_id: str | None = Field(default=None, max_length=64)


class TicketRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    public_id: str
    customer_id: str
    order_id: str | None
    category: TicketCategory | None
    status: TicketStatus
    priority: TicketPriority
    summary: str | None
    customer_replied: bool
    created_at: datetime
    updated_at: datetime


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ticket_id: uuid.UUID
    sender: MessageSender
    content: str
    created_at: datetime


class AuditEntryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    actor: str
    action: str
    tool_name: str | None
    input: dict[str, Any]
    output: dict[str, Any]
    status: str
    created_at: datetime


class AuditEntryListItem(AuditEntryRead):
    ticket_public_id: str | None
    actor_label: str


class TicketListItem(TicketRead):
    customer_name: str | None = Field(
        default=None, description="From the commerce API; missing if it is unavailable"
    )


class CustomerSummary(BaseModel):
    """What staff see about the customer; contact details are deliberately left out."""

    id: str
    name: str
    tier: Literal["standard", "gold", "platinum"]
    fraud_flag: bool
    member_since: datetime


class OrderBrief(BaseModel):
    order_id: str
    status: OrderStatus
    total: str
    placed_at: datetime
    items: list[str]


class CustomerContext(BaseModel):
    customer: CustomerSummary
    order: Order | None
    recent_orders: list[OrderBrief]


class TicketDetail(BaseModel):
    """Staff get everything. Customers get the ticket and its messages only."""

    ticket: TicketRead
    messages: list[MessageRead]
    approvals: list[ApprovalRead] = Field(default_factory=list)
    trace: list[AuditEntryRead] = Field(default_factory=list)
    customer_context: CustomerContext | None = None
    customer_context_error: str | None = None
