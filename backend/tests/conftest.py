"""Shared fixtures. Integration tests use a dedicated database, rebuilt and migrated per run."""

import os

os.environ["APP_ENV"] = "test"
os.environ["BCRYPT_ROUNDS"] = "4"
os.environ.setdefault("JWT_SECRET", "test-only-secret-with-at-least-32-characters")
# Fail if a checkpoint ever holds anything but plain JSON.
os.environ["LANGGRAPH_STRICT_MSGPACK"] = "true"

import asyncio
import importlib.util
import sys
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from langchain_core.embeddings import Embeddings
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.agents.checkpoint import open_checkpointer
from app.agents.graph import build_graph
from app.agents.llm import StructuredGenerator
from app.agents.runner import AgentRunner
from app.agents.runtime import EventBus
from app.agents.tools.commerce import CommerceClient
from app.api.deps import get_agent_runner
from app.core.config import Settings, get_settings
from app.core.security import Role, create_access_token, hash_password
from app.db.models import User
from app.db.session import get_session
from app.main import create_app
from app.rag.embeddings import get_embeddings
from app.rag.ingest import ingest_policies
from app.services.audit_service import AuditService
from tests.agent_support import PEOPLE, Api, ScriptedLLM

BACKEND_DIR = Path(__file__).resolve().parents[1]
MOCK_COMMERCE_MAIN = BACKEND_DIR.parent / "mock_commerce" / "main.py"
TEST_PASSWORD = "correct-horse-battery-staple"
TEST_CUSTOMER_ID = "cus_001"

CommerceFactory = Callable[..., CommerceClient]


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


@pytest.fixture
def session_factory(db_session: AsyncSession) -> async_sessionmaker[AsyncSession]:
    """Extra sessions on the test's connection, so their commits are rolled back too."""
    return async_sessionmaker(
        bind=db_session.bind, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )


@pytest.fixture
def audit(session_factory: async_sessionmaker[AsyncSession]) -> AuditService:
    return AuditService(session_factory)


@pytest.fixture(scope="session")
def embeddings() -> Embeddings:
    """The real embedding model, baked into the image, loaded once per run."""
    return get_embeddings()


@pytest.fixture(scope="session")
def mock_commerce() -> ModuleType:
    """The real mock commerce app, imported from the sibling service so tests can run it
    in-process and force failures deterministically."""
    if not MOCK_COMMERCE_MAIN.exists():
        raise RuntimeError(
            f"Mock commerce code not found at {MOCK_COMMERCE_MAIN}. Run tests with `make test`, "
            "which mounts it through docker-compose.test.yml."
        )
    name = "mock_commerce_main"
    spec = importlib.util.spec_from_file_location(name, MOCK_COMMERCE_MAIN)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load the mock commerce app from {MOCK_COMMERCE_MAIN}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def mock_store(mock_commerce: ModuleType) -> Any:
    """Fresh seeded commerce state for each test."""
    return mock_commerce.load_store(mock_commerce.SEED_PATH, datetime.now(UTC))


@pytest.fixture
async def make_commerce(
    mock_commerce: ModuleType, mock_store: Any
) -> AsyncIterator[CommerceFactory]:
    """Build commerce clients wired to the in-process mock.

    Keyword arguments become mock fault settings (failure_rate, slow_fraction, slow_seconds,
    random_seed). `wrap` puts a test transport, such as a request counter, in front of it.
    """
    clients: list[CommerceClient] = []

    def factory(
        *,
        timeout_seconds: float = 2.0,
        max_attempts: int = 3,
        wrap: Callable[[httpx.AsyncBaseTransport], httpx.AsyncBaseTransport] | None = None,
        **faults: Any,
    ) -> CommerceClient:
        settings = mock_commerce.MockSettings(
            **({"failure_rate": 0.0, "slow_fraction": 0.5, "slow_seconds": 0.0} | faults)
        )
        transport: httpx.AsyncBaseTransport = ASGITransport(
            app=mock_commerce.create_app(settings, mock_store)
        )
        client = CommerceClient(
            "http://mock-commerce",
            timeout_seconds=timeout_seconds,
            max_attempts=max_attempts,
            backoff_seconds=0,
            transport=wrap(transport) if wrap else transport,
        )
        clients.append(client)
        return client

    yield factory
    for client in clients:
        await client.aclose()


@pytest.fixture
async def live_sessions(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Sessions that really commit, for code that runs on its own connections (the agent).

    Everything except the policy index is emptied afterwards.
    """
    engine = create_async_engine(database_url, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE users, tickets CASCADE"))
    await engine.dispose()


@pytest.fixture(scope="session")
def policy_index(database_url: str, embeddings: Embeddings) -> None:
    """The real policies, embedded and committed once for the whole run."""

    async def load() -> None:
        engine = create_async_engine(database_url, poolclass=NullPool)
        async with async_sessionmaker(engine)() as session:
            await ingest_policies(session, embeddings)
            await session.commit()
        await engine.dispose()

    asyncio.run(load())


@pytest.fixture
async def checkpointer(database_url: str) -> AsyncIterator[AsyncPostgresSaver]:
    async with open_checkpointer(database_url) as saver:
        yield saver


@pytest.fixture
def scripted_llm() -> ScriptedLLM:
    return ScriptedLLM()


@pytest.fixture
def make_agent(
    live_sessions: async_sessionmaker[AsyncSession],
    make_commerce: CommerceFactory,
    embeddings: Embeddings,
    scripted_llm: ScriptedLLM,
    checkpointer: AsyncPostgresSaver,
    policy_index: None,
) -> Callable[..., AgentRunner]:
    """Agent runners on the test database, in-process mock shop, and scripted model."""

    def factory(
        *,
        saver: AsyncPostgresSaver | None = None,
        settings: Settings | None = None,
        llm: StructuredGenerator | None = None,
        commerce: CommerceClient | None = None,
        **faults: Any,
    ) -> AgentRunner:
        return AgentRunner(
            graph=build_graph().compile(checkpointer=saver or checkpointer),
            session_factory=live_sessions,
            commerce=commerce or make_commerce(**faults),
            audit=AuditService(live_sessions),
            llm=llm or scripted_llm,
            embeddings=embeddings,
            settings=settings or get_settings(),
            events=EventBus(),
        )

    return factory


@pytest.fixture
def agent(make_agent: Callable[..., AgentRunner]) -> AgentRunner:
    return make_agent()


@pytest.fixture
async def api(
    live_sessions: async_sessionmaker[AsyncSession], agent: AgentRunner
) -> AsyncIterator[Api]:
    """The real app with real commits, the test agent, and a signed-in user for each role."""
    app = create_app()

    async def session_override() -> AsyncIterator[AsyncSession]:
        async with live_sessions() as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_agent_runner] = lambda: agent

    tokens = {}
    async with live_sessions() as session:
        for who, (role, customer_id) in PEOPLE.items():
            user = User(
                email=f"{who}@acme.com",
                password_hash=hash_password(TEST_PASSWORD, rounds=4),
                role=role,
                customer_id=customer_id,
            )
            session.add(user)
            await session.flush()
            tokens[who] = create_access_token(user.id, role, get_settings())[0]
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield Api(app, client, tokens)


@pytest.fixture
def eval_settings(database_url: str) -> Settings:
    url = make_url(database_url)
    eval_url = url.set(database=f"{url.database}_eval").render_as_string(hide_password=False)
    return get_settings().model_copy(
        update={
            "eval_database_url": eval_url,
            "eval_commerce_url": "http://eval-shop",
            "commerce_backoff_seconds": 0,
        }
    )


@pytest.fixture
def eval_shop(mock_commerce: ModuleType) -> httpx.AsyncBaseTransport:
    """An evaluation shop: admin endpoints on, so each case loads its own data."""
    return ASGITransport(
        app=mock_commerce.create_app(mock_commerce.MockSettings(admin_enabled=True))
    )
