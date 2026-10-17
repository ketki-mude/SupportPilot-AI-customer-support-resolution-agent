"""Alembic environment: runs migrations over the async engine."""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.db.models import Base

config = context.config
# Programmatic callers (the test suite) pass the URL and keep their own logging setup.
if config.config_file_name is not None and "database_url" not in config.attributes:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata

# LangGraph's checkpointer creates and migrates its own tables; Alembic leaves them alone.
_EXTERNAL_TABLE_PREFIX = "checkpoint"


def _include_name(name: str | None, type_: str, _: object) -> bool:
    return not (type_ == "table" and name is not None and name.startswith(_EXTERNAL_TABLE_PREFIX))


def _database_url() -> str:
    url: str | None = config.attributes.get("database_url")
    return url or get_settings().database_url


def run_migrations_offline() -> None:
    """Emit SQL without a database connection."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_sync_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        include_name=_include_name,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Connect with asyncpg and run migrations inside a single transaction."""
    engine = create_async_engine(_database_url(), poolclass=NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(_run_sync_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
