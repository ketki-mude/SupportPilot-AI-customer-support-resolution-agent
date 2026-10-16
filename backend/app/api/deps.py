"""Shared FastAPI dependencies: settings, database session, current user, and role checks."""

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.security import Role, TokenError, decode_access_token
from app.db.models import User
from app.db.session import get_session

SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]

_bearer = HTTPBearer(auto_error=False)

STAFF_ROLES = (Role.AGENT, Role.SUPERVISOR, Role.ADMIN)
SUPERVISOR_ROLES = (Role.SUPERVISOR, Role.ADMIN)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    session: SessionDep,
    settings: SettingsDep,
) -> User:
    """Resolve the bearer token to a user. The database role is authoritative."""
    if credentials is None:
        raise UnauthorizedError("Missing bearer token")
    try:
        claims = decode_access_token(credentials.credentials, settings)
    except TokenError as exc:
        raise UnauthorizedError("Invalid or expired token", code="invalid_token") from exc
    user = await session.get(User, claims.user_id)
    if user is None:
        raise UnauthorizedError("Invalid or expired token", code="invalid_token")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_roles(*roles: Role) -> Callable[[User], Awaitable[User]]:
    """Build a dependency that admits only users holding one of `roles`."""
    allowed = frozenset(roles)

    async def check_role(user: CurrentUser) -> User:
        if user.role not in allowed:
            raise ForbiddenError("You do not have permission to perform this action")
        return user

    return check_role


CustomerUser = Annotated[User, Depends(require_roles(Role.CUSTOMER))]
StaffUser = Annotated[User, Depends(require_roles(*STAFF_ROLES))]
SupervisorUser = Annotated[User, Depends(require_roles(*SUPERVISOR_ROLES))]
AdminUser = Annotated[User, Depends(require_roles(Role.ADMIN))]
