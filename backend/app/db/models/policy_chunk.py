"""Embedded policy sections used for retrieval."""

from pgvector.sqlalchemy import Vector
from sqlalchemy import Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin

EMBEDDING_DIMENSIONS = 384


class PolicyChunk(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """A section of a policy document and its embedding."""

    __tablename__ = "policy_chunks"
    __table_args__ = (
        Index(
            "ix_policy_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    source: Mapped[str] = mapped_column(String(128), index=True)
    section: Mapped[str] = mapped_column(String(256))
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
