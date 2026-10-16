"""Shared fixtures. Integration tests use a dedicated database, rebuilt and migrated per run."""

import os

os.environ["APP_ENV"] = "test"
os.environ["BCRYPT_ROUNDS"] = "4"
os.environ.setdefault("JWT_SECRET", "test-only-secret-with-at-least-32-characters")

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.security import Role, hash_password
from app.db.models import User
from app.db.session import get_session
from app.main import create_app

BACKEND_DIR = Path(__file__).resolve().parents[1]
TEST_PASSWORD = "correct-horse-battery-staple"
TEST_CUSTOMER_ID = "cus_001"


def _test_database_url() -> str:
    settings = get_settings()
    if settings.test_database_url:
        return settings.test_database_url
    url = make_url(settings.database_url)
    return url.set(database=f"{url.database}_test").render_as_string(hide_password=False)


async def _recreate_database(url: str) -> None:
    target = make_url(url)
    name = target.database or ""
    if not name.endswith("_test"):
        raise RuntimeError(f"Refusing to recreate {name!r}: test database names must end in _test")
    admin = create_async_engine(
        target.set(database="postgres"), poolclass=NullPool, isolation_level="AUTOCOMMIT"
    )
    async with admin.connect() as connection:
        quoted = connection.dialect.identifier_preparer.quote(name)
        await connection.execute(text(f"DROP DATABASE IF EXISTS {quoted} WITH (FORCE)"))
        await connection.execute(text(f"CREATE DATABASE {quoted}"))
    await admin.dispose()


def alembic_config(database_url: str) -> Config:
    """Alembic config aimed at the test database."""
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.attributes["database_url"] = database_url
    return config


@pytest.fixture(scope="session")
def database_url() -> str:
    """A fresh test database migrated to head through the real Alembic migrations."""
    url = _test_database_url()
    asyncio.run(_recreate_database(url))
    command.upgrade(alembic_config(url), "head")
    return url


@pytest.fixture
async def db_session(database_url: str) -> AsyncIterator[AsyncSession]:
    """A session inside a transaction that is rolled back after the test."""
    engine = create_async_engine(database_url, poolclass=NullPool)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()
    await engine.dispose()


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """HTTP client for the real app, sharing the test's rolled-back session."""
    app = create_app()

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = override_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http


@pytest.fixture
async def users(db_session: AsyncSession) -> dict[Role, User]:
    """One user per role, all with TEST_PASSWORD."""
    password_hash = hash_password(TEST_PASSWORD, rounds=4)
    created = {
        role: User(
            email=f"{role.value}@acme.com",
            password_hash=password_hash,
            role=role,
            customer_id=TEST_CUSTOMER_ID if role is Role.CUSTOMER else None,
        )
        for role in Role
    }
    db_session.add_all(created.values())
    await db_session.flush()
    return created
