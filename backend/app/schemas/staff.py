"""Requests and responses for staff handling a ticket by hand."""

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.ticket import MessageRead, TicketRead

StaffActionKind = Literal["refund", "replacement", "cancel"]
Verdict = Literal["allowed", "override", "blocked"]
OrderId = Annotated[str, Field(pattern=r"^ORD-\d{4,10}$")]
Sku = Annotated[str, Field(min_length=1, max_length=64)]

# A reason this short ("ok", "fine") would not tell an auditor anything.
MIN_OVERRIDE_REASON = 10


class StaffReplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=2000)

    @field_validator("message")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("The message is empty")
        return value.strip()


class StaffActionCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: StaffActionKind
    order_id: OrderId | None = Field(default=None, description="Defaults to the ticket's order")
    skus: list[Sku] = Field(
        default_factory=list, max_length=50, description="Items to refund or replace; empty: all"
    )


class StaffActionRequest(StaffActionCheckRequest):
    note: str | None = Field(
        default=None,
        max_length=1000,
        description=f"Why. Required, at least {MIN_OVERRIDE_REASON} characters, for an override",
    )


class ActionCheck(BaseModel):
    """What the business rules say about an action, before anyone takes it."""

    action: StaffActionKind
    order_id: str | None
    verdict: Verdict
    rule: str
    reason: str
    amount: Decimal | None = Field(description="Computed from the order record")
    skus: list[str]


class StaffActionResult(BaseModel):
    ticket: TicketRead
    message: MessageRead
    check: ActionCheck
    reference: str | None
    amount: Decimal | None


class StaffCloseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["resolved", "declined"]
    note: str | None = Field(default=None, max_length=1000, description="For the audit log")
