"""The Postgres checkpointer that lets paused runs survive restarts."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from sqlalchemy.engine import make_url


def checkpoint_conninfo(database_url: str) -> str:
    """The SQLAlchemy URL rewritten for psycopg, which the checkpointer uses."""
    url = make_url(database_url).set(drivername="postgresql")
    return url.render_as_string(hide_password=False)


@asynccontextmanager
async def open_checkpointer(database_url: str) -> AsyncIterator[AsyncPostgresSaver]:
    """A connection pool and saver whose tables are created or migrated on first use."""
    pool = AsyncConnectionPool(
        checkpoint_conninfo(database_url),
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    async with pool:
        saver = AsyncPostgresSaver(pool)  # type: ignore[arg-type]
        await saver.setup()
        yield saver
