"""Approval requests and decisions."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import ApprovalStatus


class ApprovalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ticket_id: uuid.UUID
    proposed_action: dict[str, Any]
    status: ApprovalStatus
    decided_by: uuid.UUID | None
    note: str | None
    decided_at: datetime | None
    created_at: datetime


class ApprovalListItem(ApprovalRead):
    ticket_public_id: str


class ApprovalDecisionRequest(BaseModel):
    decision: Literal["approve", "reject"]
    note: str | None = Field(default=None, max_length=1000)
