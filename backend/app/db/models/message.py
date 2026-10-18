"""Conversation messages on a ticket."""

import uuid
from enum import StrEnum

from sqlalchemy import ForeignKey, Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin, str_enum


class MessageSender(StrEnum):
    CUSTOMER = "customer"
    AGENT = "agent"
    SUPERVISOR = "supervisor"
    # A person on the support team replying or acting on a ticket the agent handed over.
    STAFF = "staff"
    SYSTEM = "system"


class Message(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """A single message in a ticket's conversation."""

    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_ticket_id_created_at", "ticket_id", "created_at"),)

    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id"))
    sender: Mapped[MessageSender] = mapped_column(str_enum(MessageSender, "message_sender"))
    content: Mapped[str] = mapped_column(Text)
