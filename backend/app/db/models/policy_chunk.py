"""Embedded policy sections used for retrieval."""

from pgvector.sqlalchemy import Vector
from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin

EMBEDDING_DIMENSIONS = 384


class PolicyChunk(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """A section of a policy document and its embedding.

    Searched exactly, with no approximate (HNSW) index: a few dozen chunks scan in microseconds,
    and an approximate index can miss live rows after re-ingestion until it is vacuumed. Add one
    only if the corpus grows to tens of thousands of chunks.
    """

    __tablename__ = "policy_chunks"

    source: Mapped[str] = mapped_column(String(128), index=True)
    section: Mapped[str] = mapped_column(String(256))
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
