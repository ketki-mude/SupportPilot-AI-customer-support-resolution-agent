"""Role-based access: which roles each dependency admits."""

import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.deps import AdminUser, CustomerUser, StaffUser, SupervisorUser, get_current_user
from app.core.errors import register_exception_handlers
from app.core.security import Role
from app.db.models import User


def _app_as(role: Role) -> FastAPI:
    """A throwaway app with one route per guard, authenticated as a user with `role`."""
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/customer")
    async def customer_only(user: CustomerUser) -> dict[str, str]:
        return {"role": user.role}

    @app.get("/staff")
    async def staff_only(user: StaffUser) -> dict[str, str]:
        return {"role": user.role}

    @app.get("/supervisor")
    async def supervisor_only(user: SupervisorUser) -> dict[str, str]:
        return {"role": user.role}

    @app.get("/admin")
    async def admin_only(user: AdminUser) -> dict[str, str]:
        return {"role": user.role}

    user = User(id=uuid.uuid4(), email=f"{role}@acme.com", password_hash="x", role=role)
    app.dependency_overrides[get_current_user] = lambda: user
    return app


ACCESS = {
    Role.CUSTOMER: {"/customer"},
    Role.AGENT: {"/staff"},
    Role.SUPERVISOR: {"/staff", "/supervisor"},
    Role.ADMIN: {"/staff", "/supervisor", "/admin"},
}


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize("path", ["/customer", "/staff", "/supervisor", "/admin"])
async def test_role_matrix(role: Role, path: str) -> None:
    transport = ASGITransport(app=_app_as(role))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(path)
    if path in ACCESS[role]:
        assert response.status_code == 200
        assert response.json() == {"role": role.value}
    else:
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "forbidden"
