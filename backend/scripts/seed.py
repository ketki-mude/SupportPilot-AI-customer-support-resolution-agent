"""Create or update the four demo users. Passwords come from SEED_*_PASSWORD env vars.

Run with: python -m scripts.seed
"""

import asyncio
import sys
from dataclasses import dataclass

import structlog
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.core.security import Role, hash_password
from app.db.models import User
from app.db.session import get_engine, get_session_factory

logger = structlog.get_logger(__name__)

# Matches the customer who owns the demo orders in mock_commerce/seed_data.json.
DEMO_CUSTOMER_ID = "cus_001"


@dataclass(frozen=True, slots=True)
class DemoAccount:
    email: str
    role: Role
    password_env: str
    customer_id: str | None = None

    def password(self, settings: Settings) -> SecretStr | None:
        """Read this account's password from settings."""
        value: SecretStr | None = getattr(settings, self.password_env.lower())
        return value


DEMO_ACCOUNTS = (
    DemoAccount("customer@demo.com", Role.CUSTOMER, "SEED_CUSTOMER_PASSWORD", DEMO_CUSTOMER_ID),
    DemoAccount("agent@demo.com", Role.AGENT, "SEED_AGENT_PASSWORD"),
    DemoAccount("supervisor@demo.com", Role.SUPERVISOR, "SEED_SUPERVISOR_PASSWORD"),
    DemoAccount("admin@demo.com", Role.ADMIN, "SEED_ADMIN_PASSWORD"),
)


class SeedError(Exception):
    """Raised when seeding cannot proceed, for example missing passwords."""


async def seed_users(session: AsyncSession, settings: Settings) -> list[User]:
    """Upsert every demo account so re-running the seed is safe and rotates passwords."""
    passwords: dict[DemoAccount, str] = {}
    missing: list[str] = []
    for account in DEMO_ACCOUNTS:
        secret = account.password(settings)
        if secret is None:
            missing.append(account.password_env)
        else:
            passwords[account] = secret.get_secret_value()
    if missing:
        raise SeedError(f"Set these environment variables before seeding: {', '.join(missing)}")

    users: list[User] = []
    for account, password in passwords.items():
        password_hash = await asyncio.to_thread(
            hash_password, password, rounds=settings.bcrypt_rounds
        )
        user = await session.scalar(select(User).where(User.email == account.email))
        if user is None:
            user = User(email=account.email)
            session.add(user)
        user.role = account.role
        user.customer_id = account.customer_id
        user.password_hash = password_hash
        users.append(user)
    await session.flush()
    return users


async def _run(settings: Settings) -> None:
    async with get_session_factory()() as session:
        users = await seed_users(session, settings)
        await session.commit()
    await get_engine().dispose()
    for user in users:
        logger.info("seeded_user", email=user.email, role=user.role, customer_id=user.customer_id)


def main() -> int:
    """Entry point. Returns a process exit code."""
    settings = get_settings()
    configure_logging(settings)
    try:
        asyncio.run(_run(settings))
    except SeedError as exc:
        logger.error("seed_failed", error=str(exc))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
