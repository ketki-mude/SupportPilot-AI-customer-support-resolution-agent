"""Initial schema: pgvector, users, tickets, messages, approvals, audit logs, runs, policy chunks.

Revision ID: 0001
Revises:
Create Date: 2026-10-16
"""

import uuid
from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _id() -> sa.Column[uuid.UUID]:
    return sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False)


def _created_at() -> sa.Column[datetime]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def _one_of(column: str, values: tuple[str, ...], name: str) -> sa.CheckConstraint:
    allowed = ", ".join(f"'{value}'" for value in values)
    return sa.CheckConstraint(f"{column} IN ({allowed})", name=op.f(name))


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "users",
        _id(),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(128), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("customer_id", sa.String(64), nullable=True),
        _created_at(),
        _one_of("role", ("customer", "agent", "supervisor", "admin"), "ck_users_user_role"),
        sa.CheckConstraint(
            "(role = 'customer') = (customer_id IS NOT NULL)", name=op.f("ck_users_customer_link")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )

    op.execute("CREATE SEQUENCE ticket_public_id_seq START WITH 1000")
    op.create_table(
        "tickets",
        _id(),
        sa.Column(
            "public_id",
            sa.String(16),
            server_default=sa.text("'TCK-' || nextval('ticket_public_id_seq')"),
            nullable=False,
        ),
        sa.Column("customer_id", sa.String(64), nullable=False),
        sa.Column("order_id", sa.String(64), nullable=True),
        sa.Column("category", sa.String(32), nullable=True),
        sa.Column("status", sa.String(32), server_default="open", nullable=False),
        sa.Column("priority", sa.String(32), server_default="normal", nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        _created_at(),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        _one_of(
            "category",
            ("refund", "missing_order", "cancellation", "billing", "other"),
            "ck_tickets_ticket_category",
        ),
        _one_of(
            "status",
            ("open", "awaiting_approval", "resolved", "escalated", "rejected"),
            "ck_tickets_ticket_status",
        ),
        _one_of("priority", ("low", "normal", "high"), "ck_tickets_ticket_priority"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tickets")),
        sa.UniqueConstraint("public_id", name=op.f("uq_tickets_public_id")),
    )
    op.execute("ALTER SEQUENCE ticket_public_id_seq OWNED BY tickets.public_id")
    op.create_index(op.f("ix_tickets_customer_id"), "tickets", ["customer_id"])
    op.create_index(op.f("ix_tickets_status"), "tickets", ["status"])

    op.create_table(
        "messages",
        _id(),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("sender", sa.String(32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        _created_at(),
        _one_of(
            "sender", ("customer", "agent", "supervisor", "system"), "ck_messages_message_sender"
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"], ["tickets.id"], name=op.f("fk_messages_ticket_id_tickets")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_messages")),
    )
    op.create_index("ix_messages_ticket_id_created_at", "messages", ["ticket_id", "created_at"])

    op.create_table(
        "approvals",
        _id(),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("proposed_action", JSONB(), nullable=False),
        sa.Column("status", sa.String(32), server_default="pending", nullable=False),
        sa.Column("decided_by", sa.Uuid(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        _one_of("status", ("pending", "approved", "rejected"), "ck_approvals_approval_status"),
        sa.CheckConstraint(
            "(status = 'pending' AND decided_by IS NULL AND decided_at IS NULL)"
            " OR (status <> 'pending' AND decided_by IS NOT NULL AND decided_at IS NOT NULL)",
            name=op.f("ck_approvals_decision_recorded"),
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"], ["tickets.id"], name=op.f("fk_approvals_ticket_id_tickets")
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"], ["users.id"], name=op.f("fk_approvals_decided_by_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approvals")),
    )
    op.create_index(op.f("ix_approvals_ticket_id"), "approvals", ["ticket_id"])
    op.create_index(op.f("ix_approvals_status"), "approvals", ["status"])
    op.create_index(
        "uq_approvals_one_pending_per_ticket",
        "approvals",
        ["ticket_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )

    op.create_table(
        "audit_logs",
        _id(),
        sa.Column("ticket_id", sa.Uuid(), nullable=True),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("tool_name", sa.String(64), nullable=True),
        sa.Column("input", JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("output", JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(
            ["ticket_id"], ["tickets.id"], name=op.f("fk_audit_logs_ticket_id_tickets")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )
    op.create_index("ix_audit_logs_ticket_id_created_at", "audit_logs", ["ticket_id", "created_at"])
    # Append-only: the database itself refuses to change or remove audit history.
    op.execute(
        """
        CREATE FUNCTION audit_logs_reject_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_logs is append-only: % is not allowed', TG_OP;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_logs_append_only
        BEFORE UPDATE OR DELETE ON audit_logs
        FOR EACH ROW EXECUTE FUNCTION audit_logs_reject_mutation()
        """
    )

    op.create_table(
        "agent_runs",
        _id(),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("thread_id", sa.String(64), nullable=False),
        sa.Column("input_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cost_usd", sa.Numeric(12, 6), server_default="0", nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(32), server_default="running", nullable=False),
        _created_at(),
        _one_of(
            "status",
            ("running", "paused", "completed", "failed"),
            "ck_agent_runs_agent_run_status",
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"], ["tickets.id"], name=op.f("fk_agent_runs_ticket_id_tickets")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_runs")),
    )
    op.create_index(op.f("ix_agent_runs_ticket_id"), "agent_runs", ["ticket_id"])
    op.create_index(op.f("ix_agent_runs_thread_id"), "agent_runs", ["thread_id"])

    op.create_table(
        "policy_chunks",
        _id(),
        sa.Column("source", sa.String(128), nullable=False),
        sa.Column("section", sa.String(256), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(384), nullable=False),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_policy_chunks")),
    )
    op.create_index(op.f("ix_policy_chunks_source"), "policy_chunks", ["source"])
    op.create_index(
        "ix_policy_chunks_embedding_hnsw",
        "policy_chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_table("policy_chunks")
    op.drop_table("agent_runs")
    op.drop_table("audit_logs")
    op.execute("DROP FUNCTION audit_logs_reject_mutation()")
    op.drop_table("approvals")
    op.drop_table("messages")
    op.drop_table("tickets")  # also drops the owned ticket_public_id_seq
    op.drop_table("users")
    # The vector extension is left installed; other database objects may depend on it.
