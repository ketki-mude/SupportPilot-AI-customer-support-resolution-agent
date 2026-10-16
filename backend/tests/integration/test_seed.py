"""Demo user seeding."""

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.security import Role, verify_password
from app.db.models import User
from scripts.seed import DEMO_CUSTOMER_ID, SeedError, seed_users

PASSWORDS = {
    "seed_customer_password": SecretStr("customer-pass"),
    "seed_agent_password": SecretStr("agent-pass"),
    "seed_supervisor_password": SecretStr("supervisor-pass"),
    "seed_admin_password": SecretStr("admin-pass"),
}


@pytest.fixture
def seed_settings() -> Settings:
    return get_settings().model_copy(update=PASSWORDS)


async def test_seed_creates_the_four_demo_users(
    db_session: AsyncSession, seed_settings: Settings
) -> None:
    await seed_users(db_session, seed_settings)
    users = {u.email: u for u in await db_session.scalars(select(User))}
    assert {email: u.role for email, u in users.items()} == {
        "customer@demo.com": Role.CUSTOMER,
        "agent@demo.com": Role.AGENT,
        "supervisor@demo.com": Role.SUPERVISOR,
        "admin@demo.com": Role.ADMIN,
    }
    assert users["customer@demo.com"].customer_id == DEMO_CUSTOMER_ID
    assert users["admin@demo.com"].customer_id is None
    assert verify_password("supervisor-pass", users["supervisor@demo.com"].password_hash)


async def test_seed_is_idempotent_and_rotates_passwords(
    db_session: AsyncSession, seed_settings: Settings
) -> None:
    await seed_users(db_session, seed_settings)
    rotated = seed_settings.model_copy(update={"seed_agent_password": SecretStr("new-pass")})
    await seed_users(db_session, rotated)

    assert await db_session.scalar(select(func.count()).select_from(User)) == 4
    agent = await db_session.scalar(select(User).where(User.email == "agent@demo.com"))
    assert agent is not None
    assert verify_password("new-pass", agent.password_hash)


async def test_seed_refuses_to_run_without_passwords(db_session: AsyncSession) -> None:
    settings = get_settings().model_copy(
        update=PASSWORDS | {"seed_admin_password": None, "seed_agent_password": None}
    )
    with pytest.raises(SeedError, match="SEED_AGENT_PASSWORD, SEED_ADMIN_PASSWORD"):
        await seed_users(db_session, settings)
    assert await db_session.scalar(select(func.count()).select_from(User)) == 0
