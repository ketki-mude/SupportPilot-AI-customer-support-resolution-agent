"""Embed the policy documents into policy_chunks, replacing whatever was there."""

from pathlib import Path

from langchain_core.embeddings import Embeddings
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import EMBEDDING_DIMENSIONS, PolicyChunk
from app.rag.chunking import PolicySection, chunk_policy

POLICY_DIR = Path(__file__).resolve().parents[2] / "data" / "policies"


def load_sections(directory: Path = POLICY_DIR) -> list[PolicySection]:
    """Chunk every markdown file in the directory, in a stable order."""
    files = sorted(directory.glob("*.md"))
    if not files:
        raise FileNotFoundError(f"No policy documents found in {directory}")
    return [section for path in files for section in chunk_policy(path.name, path.read_text())]


async def ingest_policies(
    session: AsyncSession, embeddings: Embeddings, directory: Path = POLICY_DIR
) -> dict[str, int]:
    """Replace all chunks in one transaction and return the chunk count per document.

    Readers never see a half-loaded index because the caller commits once at the end.
    """
    sections = load_sections(directory)
    vectors = await embeddings.aembed_documents([s.embedding_text for s in sections])
    if any(len(vector) != EMBEDDING_DIMENSIONS for vector in vectors):
        raise ValueError(
            f"Embedding model must produce {EMBEDDING_DIMENSIONS} dimensions to match the schema"
        )

    await session.execute(delete(PolicyChunk))
    session.add_all(
        PolicyChunk(source=s.source, section=s.label, content=s.content, embedding=vector)
        for s, vector in zip(sections, vectors, strict=True)
    )
    await session.flush()

    counts: dict[str, int] = {}
    for section in sections:
        counts[section.source] = counts.get(section.source, 0) + 1
    return counts
