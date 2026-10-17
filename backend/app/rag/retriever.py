"""Find the policy sections most relevant to a question, with citations."""

from langchain_core.embeddings import Embeddings
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PolicyChunk
from app.schemas.agent import PolicySnippet


async def retrieve_policies(
    session: AsyncSession,
    embeddings: Embeddings,
    query: str,
    *,
    top_k: int,
    min_similarity: float,
) -> list[PolicySnippet]:
    """Top-k sections by exact cosine similarity, dropping any below `min_similarity`.

    Fewer than `top_k` results, or none, come back for questions the policies do not cover.
    """
    vector = await embeddings.aembed_query(query)
    distance = PolicyChunk.embedding.cosine_distance(vector).label("distance")
    rows = await session.execute(
        select(PolicyChunk.source, PolicyChunk.section, PolicyChunk.content, distance)
        .order_by(distance)
        .limit(top_k)
    )
    snippets = [
        PolicySnippet(source=source, section=section, content=content, score=1 - dist)
        for source, section, content, dist in rows
    ]
    return [snippet for snippet in snippets if snippet.score >= min_similarity]
