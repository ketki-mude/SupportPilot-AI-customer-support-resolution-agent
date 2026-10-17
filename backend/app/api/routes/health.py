"""Liveness and dependency health."""

import asyncio

import structlog
from fastapi import APIRouter, Request, Response, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.deps import SessionDep, SettingsDep
from app.schemas.health import HealthResponse

router = APIRouter(tags=["health"])
logger = structlog.get_logger(__name__)

DB_CHECK_TIMEOUT_SECONDS = 3


@router.get("/health", responses={503: {"model": HealthResponse}})
async def health(
    request: Request, session: SessionDep, settings: SettingsDep, response: Response
) -> HealthResponse:
    """Report whether the API and its database are reachable. Returns 503 when degraded."""
    runner = getattr(request.app.state, "agent_runner", None)
    circuit = runner.commerce.breaker.state.value if runner else None
    try:
        async with asyncio.timeout(DB_CHECK_TIMEOUT_SECONDS):
            await session.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError, TimeoutError) as exc:
        logger.warning("health_db_unavailable", error=str(exc))
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(
            status="degraded",
            database="unavailable",
            version=settings.app_version,
            commerce_circuit=circuit,
        )
    return HealthResponse(
        status="ok", database="ok", version=settings.app_version, commerce_circuit=circuit
    )
