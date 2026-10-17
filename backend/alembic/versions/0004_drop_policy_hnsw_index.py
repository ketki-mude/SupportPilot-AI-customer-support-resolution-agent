"""Drop the HNSW index on policy chunks in favour of exact search.

With a few dozen chunks an exact scan is fast and never misses, while the approximate index
returned no rows after repeated re-ingestion left its graph full of deleted entries.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX = "ix_policy_chunks_embedding_hnsw"


def upgrade() -> None:
    op.drop_index(INDEX, table_name="policy_chunks")


def downgrade() -> None:
    op.create_index(
        INDEX,
        "policy_chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
