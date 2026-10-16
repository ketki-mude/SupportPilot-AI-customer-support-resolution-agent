"""Fixtures: a fresh seeded store and an in-process client per test."""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from main import SEED_PATH, CommerceStore, MockSettings, create_app, load_store

NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)

ClientFactory = Callable[..., AbstractAsyncContextManager[AsyncClient]]


def mock_settings(**overrides: Any) -> MockSettings:
    """Explicit settings so values from the container environment never leak into tests."""
    values: dict[str, Any] = {
        "failure_rate": 0.0,
        "slow_fraction": 0.5,
        "slow_seconds": 0.0,
        "random_seed": 7,
    }
    return MockSettings(**(values | overrides))


@pytest.fixture
def store() -> CommerceStore:
    return load_store(SEED_PATH, NOW)


@pytest.fixture
def make_client(store: CommerceStore) -> ClientFactory:
    @asynccontextmanager
    async def factory(**overrides: Any) -> AsyncIterator[AsyncClient]:
        app = create_app(mock_settings(**overrides), store)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://mock") as client:
            yield client

    return factory


@pytest.fixture
async def client(make_client: ClientFactory) -> AsyncIterator[AsyncClient]:
    async with make_client() as http:
        yield http
