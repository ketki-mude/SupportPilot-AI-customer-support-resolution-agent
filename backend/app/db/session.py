"""Async SQLAlchemy engine and request-scoped sessions."""

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings


@lru_cache
def get_engine() -> AsyncEngine:
    """Create the shared engine on first use."""
    return create_async_engine(
        get_settings().database_url,
        pool_pre_ping=True,
        connect_args={"timeout": 5, "command_timeout": get_settings().db_command_timeout_seconds},
    )


@lru_cache
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Session factory bound to the shared engine."""
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request, closed (and rolled back) afterwards."""
    async with get_session_factory()() as session:
        yield session
