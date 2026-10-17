"""The evaluation harness end to end: the real graph, its own database, the in-process shop."""

from pathlib import Path
from typing import Any

import httpx
import pytest
from langchain_core.embeddings import Embeddings
from langchain_core.messages import BaseMessage
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.agents.llm import LLMError
from app.core.config import Settings, get_settings
from app.db.models import Ticket
from app.evals.dataset import Dataset, load_dataset
from app.evals.harness import evals_dir, prepare_database, run_evaluation
from app.evals.metrics import CaseResult
from app.evals.quota import RetryingLLM
from app.observability.cost import UsageTracker

DATASET = evals_dir(get_settings()) / "dataset" / "cases.jsonl"


@pytest.fixture
def dataset() -> Dataset:
    return load_dataset(DATASET)


async def _tickets(url: str) -> int:
    engine = create_async_engine(url, poolclass=NullPool)
    async with async_sessionmaker(engine)() as session:
        total = await session.scalar(select(func.count()).select_from(Ticket))
    await engine.dispose()
    return total or 0


async def test_smoke_set_passes_with_the_keyword_stand_in(
    eval_settings: Settings,
    eval_shop: httpx.AsyncBaseTransport,
    embeddings: Embeddings,
    dataset: Dataset,
    database_url: str,
    live_sessions: async_sessionmaker[AsyncSession],
) -> None:
    before = await _tickets(database_url)
    seen: list[str] = []

    async def progress(index: int, total: int, result: CaseResult) -> None:
        seen.append(f"{index}/{total} {result.id}")

    report = await run_evaluation(
        eval_settings,
        embeddings,
        dataset,
        mode="mock",
        limit=10,
        transport=eval_shop,
        on_progress=progress,
    )

    failed = {r.id: r.reasons for r in report.results if not r.passed}
    assert failed == {}
    assert report.complete and report.cases_scored == 10
    assert report.metrics.unauthorized == 0
    assert seen[0] == "1/10 refund-auto-01" and len(seen) == 10
    approved = next(r for r in report.results if r.id == "refund-approval-01")
    assert approved.observed_requires_approval and approved.observed_action == "refund"
    # Isolation: the run used its own database and left the app's tickets alone.
    assert await _tickets(eval_settings.eval_database_url or "") == 10
    assert await _tickets(database_url) == before


async def test_effects_are_read_from_the_shop(
    eval_settings: Settings,
    eval_shop: httpx.AsyncBaseTransport,
    embeddings: Embeddings,
    dataset: Dataset,
) -> None:
    """A refund the case forbids is caught from the shop's own records."""
    allowed = dataset.cases[0]
    forbidden = allowed.model_copy(
        update={"id": "forbidden", "expected_action": "deny", "must_not": ["refund"]}
    )
    trap = Dataset(path="trap.jsonl", sha256="0" * 64, cases=[forbidden])
    report = await run_evaluation(eval_settings, embeddings, trap, mode="mock", transport=eval_shop)
    [result] = report.results
    assert result.unauthorized and not result.passed
    assert "forbidden effect: refund" in result.reasons


class _DailyQuota:
    async def generate(self, schema: Any, messages: list[BaseMessage], usage: UsageTracker) -> Any:
        raise LLMError("429 RESOURCE_EXHAUSTED {'retryDelay': '28853s'}")


async def test_quota_exhaustion_stops_the_run_and_scores_nothing_it_affected(
    eval_settings: Settings,
    eval_shop: httpx.AsyncBaseTransport,
    embeddings: Embeddings,
    dataset: Dataset,
) -> None:
    report = await run_evaluation(
        eval_settings,
        embeddings,
        dataset,
        mode="live",
        limit=3,
        llm=RetryingLLM(_DailyQuota()),
        transport=eval_shop,
    )
    assert report.cases_scored == 0 and not report.complete
    assert report.stopped_reason is not None
    assert "Stopped at case 1 (refund-auto-01)" in report.stopped_reason
    assert report.estimated_cost_usd > 0


async def test_only_evaluation_databases_are_recreated(embeddings: Embeddings) -> None:
    with pytest.raises(RuntimeError, match="evaluation databases end in _eval"):
        await prepare_database("postgresql+asyncpg://u:p@db:5432/supportpilot", embeddings)


def test_dataset_path_exists() -> None:
    assert Path(DATASET).is_file()
