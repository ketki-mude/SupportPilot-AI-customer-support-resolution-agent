"""Who can reach what: every protected endpoint, every role, and forged or stale tokens."""

import uuid
from datetime import timedelta

import jwt
import pytest
from pydantic import SecretStr

from app.agents.runner import AgentRunner
from app.core.config import get_settings
from app.core.security import Role, create_access_token
from tests.agent_support import Api, ScriptedLLM, intake, resolution

PROTECTED = [
    ("GET", "/api/auth/me"),
    ("POST", "/api/chat/tickets"),
    ("GET", "/api/tickets"),
    ("GET", f"/api/tickets/{uuid.uuid4()}"),
    ("POST", f"/api/chat/tickets/{uuid.uuid4()}/messages"),
    ("GET", f"/api/chat/tickets/{uuid.uuid4()}/stream"),
    ("GET", "/api/approvals"),
    ("POST", f"/api/approvals/{uuid.uuid4()}/decision"),
    ("GET", "/api/audit"),
]


async def _paused_ticket(
    api: Api, agent: AgentRunner, scripted_llm: ScriptedLLM
) -> tuple[str, str]:
    scripted_llm.queue(intake(), resolution())
    created = await api.post(
        "/chat/tickets", "customer", {"message": "My headphones arrived damaged"}
    )
    await agent.wait()
    ticket_id = created.json()["ticket"]["id"]
    [approval] = (await api.get("/approvals", "supervisor")).json()["items"]
    return ticket_id, approval["id"]


@pytest.mark.parametrize(("method", "path"), PROTECTED)
async def test_every_protected_endpoint_needs_a_token(api: Api, method: str, path: str) -> None:
    response = await api.client.request(method, path, json={"message": "x", "decision": "approve"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


@pytest.mark.parametrize(
    "path", ["/api/approvals", f"/api/approvals/{uuid.uuid4()}/decision", "/api/audit"]
)
async def test_customers_cannot_reach_staff_endpoints(api: Api, path: str) -> None:
    method = "POST" if path.endswith("decision") else "GET"
    response = await api.client.request(
        method, path, json={"decision": "approve"}, headers=api.auth("customer")
    )
    assert response.status_code == 403


async def test_customers_cannot_read_or_write_another_customers_ticket(
    api: Api, agent: AgentRunner, scripted_llm: ScriptedLLM
) -> None:
    ticket_id, _ = await _paused_ticket(api, agent, scripted_llm)
    attempts = [
        await api.get(f"/tickets/{ticket_id}", "other_customer"),
        await api.post(f"/chat/tickets/{ticket_id}/messages", "other_customer", {"message": "hi"}),
        await api.get(f"/chat/tickets/{ticket_id}/stream", "other_customer"),
    ]
    assert [r.status_code for r in attempts] == [404, 404, 404]  # looks like it does not exist
    listed = (await api.get("/tickets", "other_customer", q=ticket_id)).json()
    assert listed["items"] == []


@pytest.mark.parametrize("who", ["customer", "agent"])
async def test_only_supervisors_and_admins_decide(
    api: Api, agent: AgentRunner, scripted_llm: ScriptedLLM, who: str
) -> None:
    _, approval_id = await _paused_ticket(api, agent, scripted_llm)
    response = await api.post(f"/approvals/{approval_id}/decision", who, {"decision": "approve"})
    assert response.status_code == 403
    [still_pending] = (await api.get("/approvals", "supervisor")).json()["items"]
    assert still_pending["status"] == "pending"


async def test_admins_can_decide(api: Api, agent: AgentRunner, scripted_llm: ScriptedLLM) -> None:
    _, approval_id = await _paused_ticket(api, agent, scripted_llm)
    response = await api.post(f"/approvals/{approval_id}/decision", "admin", {"decision": "reject"})
    await agent.wait()
    assert response.status_code == 200


async def test_staff_cannot_open_chats_as_a_customer(api: Api) -> None:
    for who in ("agent", "supervisor", "admin"):
        response = await api.post("/chat/tickets", who, {"message": "refund me"})
        assert response.status_code == 403


async def test_customers_never_see_internal_ticket_detail(
    api: Api, agent: AgentRunner, scripted_llm: ScriptedLLM
) -> None:
    ticket_id, _ = await _paused_ticket(api, agent, scripted_llm)
    detail = (await api.get(f"/tickets/{ticket_id}", "customer")).json()
    assert detail["approvals"] == detail["trace"] == []
    assert detail["customer_context"] is None and detail["usage"] is None


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_expired_access_token_is_rejected(api: Api) -> None:
    token, _ = create_access_token(
        uuid.uuid4(), Role.ADMIN, get_settings(), expires_delta=timedelta(seconds=-5)
    )
    response = await api.client.get("/api/auth/me", headers=_bearer(token))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


async def test_token_signed_with_another_key_is_rejected(api: Api) -> None:
    me = (await api.client.get("/api/auth/me", headers=api.auth("customer"))).json()
    forged_settings = get_settings().model_copy(update={"jwt_secret": SecretStr("f" * 48)})
    forged, _ = create_access_token(uuid.UUID(me["id"]), Role.ADMIN, forged_settings)
    response = await api.client.get("/api/audit", headers=_bearer(forged))
    assert response.status_code == 401


async def test_role_claim_cannot_be_raised_by_editing_the_token(api: Api) -> None:
    header, _, signature = api.tokens["customer"].split(".")
    claims = jwt.decode(api.tokens["customer"], options={"verify_signature": False})
    raised = jwt.encode({**claims, "role": "admin"}, "irrelevant-key-of-sufficient-length", "HS256")
    tampered = f"{header}.{raised.split('.')[1]}.{signature}"
    response = await api.client.get("/api/audit", headers=_bearer(tampered))
    assert response.status_code == 401


async def test_a_refresh_token_is_not_an_access_token(api: Api) -> None:
    login = await api.client.post(
        "/api/auth/login",
        json={"email": "agent@acme.com", "password": "correct-horse-battery-staple"},
    )
    refresh_value = login.cookies["sp_refresh"]
    response = await api.client.get("/api/auth/me", headers=_bearer(refresh_value))
    assert response.status_code == 401
