"""Embed backend/data/policies/*.md into the policy_chunks table.

Run with: python -m scripts.ingest_policies
"""

import asyncio
import sys

import structlog

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import get_engine, get_session_factory
from app.rag.embeddings import get_embeddings
from app.rag.ingest import ingest_policies

logger = structlog.get_logger(__name__)


async def _run() -> dict[str, int]:
    async with get_session_factory()() as session:
        counts = await ingest_policies(session, get_embeddings())
        await session.commit()
    await get_engine().dispose()
    return counts


def main() -> int:
    """Entry point. Returns a process exit code."""
    configure_logging(get_settings())
    try:
        counts = asyncio.run(_run())
    except (FileNotFoundError, ValueError) as exc:
        logger.error("ingest_failed", error=str(exc))
        return 1
    for source, chunks in counts.items():
        logger.info("ingested_policy", source=source, chunks=chunks)
    logger.info("ingest_complete", documents=len(counts), chunks=sum(counts.values()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
