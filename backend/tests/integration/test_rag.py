"""Policy ingestion and retrieval with the real embedding model and pgvector."""

import pytest
from langchain_core.embeddings import Embeddings
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import PolicyChunk
from app.rag.ingest import ingest_policies, load_sections
from app.rag.retriever import retrieve_policies
from app.schemas.agent import PolicySnippet


@pytest.fixture
async def ingested(db_session: AsyncSession, embeddings: Embeddings) -> dict[str, int]:
    return await ingest_policies(db_session, embeddings)


async def _search(
    session: AsyncSession, embeddings: Embeddings, query: str, top_k: int = 4
) -> list[PolicySnippet]:
    return await retrieve_policies(
        session,
        embeddings,
        query,
        top_k=top_k,
        min_similarity=get_settings().rag_min_similarity,
    )


async def _chunk_count(session: AsyncSession) -> int | None:
    return await session.scalar(select(func.count()).select_from(PolicyChunk))


async def test_ingest_loads_every_document(
    db_session: AsyncSession, ingested: dict[str, int]
) -> None:
    assert set(ingested) == {
        "billing_policy.md",
        "cancellation_policy.md",
        "refund_policy.md",
        "shipping_policy.md",
    }
    assert await _chunk_count(db_session) == sum(ingested.values()) == len(load_sections())


async def test_reingest_replaces_instead_of_duplicating(
    db_session: AsyncSession, embeddings: Embeddings, ingested: dict[str, int]
) -> None:
    await ingest_policies(db_session, embeddings)
    assert await _chunk_count(db_session) == sum(ingested.values())


async def test_damaged_item_query_returns_the_damaged_items_section(
    db_session: AsyncSession, embeddings: Embeddings, ingested: dict[str, int]
) -> None:
    """Acceptance: "damaged item refund" finds the refund section about damaged items."""
    results = await _search(db_session, embeddings, "damaged item refund")
    top = results[0]
    assert (top.source, top.section) == ("refund_policy.md", "3.2 Damaged or defective items")
    assert top.citation == "Refund policy, section 3.2: Damaged or defective items"


@pytest.mark.parametrize(
    ("query", "source", "section"),
    [
        (
            "Can I cancel my order before it ships?",
            "cancellation_policy.md",
            "1.1 Cancelling a processing order",
        ),
        (
            "My package is late and still has not arrived",
            "shipping_policy.md",
            "3.1 Late deliveries",
        ),
        (
            "I was charged twice for the same order",
            "billing_policy.md",
            "4.1 Duplicate or unrecognised charges",
        ),
        ("How many days do I have to ask for a refund?", "refund_policy.md", "2.2 Refund window"),
    ],
)
async def test_other_questions_find_their_section(
    db_session: AsyncSession,
    embeddings: Embeddings,
    ingested: dict[str, int],
    query: str,
    source: str,
    section: str,
) -> None:
    results = await _search(db_session, embeddings, query)
    assert (source, section) in {(r.source, r.section) for r in results}


async def test_off_topic_questions_return_nothing(
    db_session: AsyncSession, embeddings: Embeddings, ingested: dict[str, int]
) -> None:
    assert await _search(db_session, embeddings, "What is the weather in Paris tomorrow?") == []


async def test_results_are_ranked_and_capped(
    db_session: AsyncSession, embeddings: Embeddings, ingested: dict[str, int]
) -> None:
    results = await _search(db_session, embeddings, "refund for a broken item", top_k=3)
    assert 1 <= len(results) <= 3
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)
    assert all(score >= get_settings().rag_min_similarity for score in scores)


async def test_scoped_search_keeps_the_governing_sections(
    db_session: AsyncSession, embeddings: Embeddings, ingested: dict[str, int]
) -> None:
    """Customer wording can score below the floor; scoped to a document, it still finds them."""
    query = "refund: The espresso machine I received is leaking water everywhere."
    assert await _search(db_session, embeddings, query) == []
    results = await retrieve_policies(
        db_session, embeddings, query, top_k=4, min_similarity=0.0, sources=["refund_policy.md"]
    )
    assert len(results) == 4
    assert {r.source for r in results} == {"refund_policy.md"}


async def test_retrieval_survives_repeated_reingestion(
    db_session: AsyncSession, embeddings: Embeddings, ingested: dict[str, int]
) -> None:
    """Re-ingesting leaves hundreds of deleted rows behind; search must still find live ones."""
    reused = _ReusedEmbeddings(embeddings)
    for _ in range(12):
        await ingest_policies(db_session, reused)
    [top, *_] = await _search(db_session, embeddings, "damaged item refund")
    assert top.section == "3.2 Damaged or defective items"


class _ReusedEmbeddings(Embeddings):
    """Embeds each document once, so repeated ingests stay fast."""

    def __init__(self, inner: Embeddings) -> None:
        self.inner = inner
        self.vectors: dict[str, list[float]] = {}

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        missing = [t for t in texts if t not in self.vectors]
        if missing:
            self.vectors.update(zip(missing, self.inner.embed_documents(missing), strict=True))
        return [self.vectors[t] for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self.inner.embed_query(text)


class _WrongSizeEmbeddings(Embeddings):
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [0.1, 0.2, 0.3]


async def test_wrong_dimension_model_is_rejected_without_touching_the_index(
    db_session: AsyncSession, ingested: dict[str, int]
) -> None:
    with pytest.raises(ValueError, match="384 dimensions"):
        await ingest_policies(db_session, _WrongSizeEmbeddings())
    assert await _chunk_count(db_session) == sum(ingested.values())
