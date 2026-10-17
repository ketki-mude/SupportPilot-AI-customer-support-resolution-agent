"""FastAPI application factory."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from functools import partial

from fastapi import FastAPI

from app.agents.checkpoint import open_checkpointer
from app.agents.graph import build_graph
from app.agents.llm import StructuredLLM, create_chat_model
from app.agents.runner import AgentRunner
from app.agents.runtime import EventBus
from app.agents.tools.commerce import CommerceClient
from app.api.routes import api_router
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import install_middleware
from app.core.rate_limit import RateLimits
from app.db.session import get_engine, get_session_factory
from app.evals.jobs import EvalJobs
from app.observability.tracing import build_tracer, disable_ambient_tracing
from app.rag.embeddings import get_embeddings
from app.services.audit_service import AuditService

SHUTDOWN_GRACE_SECONDS = 10


def _lifespan(settings: Settings):  # type: ignore[no-untyped-def]  # FastAPI's lifespan type
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Start the agent runtime; on shutdown let in-flight runs finish briefly.

        Paused runs need nothing here: their state is already in the checkpointer.
        """
        async with (
            open_checkpointer(settings.database_url) as checkpointer,
            CommerceClient.from_settings(settings) as commerce,
        ):
            session_factory = get_session_factory()
            runner = AgentRunner(
                graph=build_graph().compile(checkpointer=checkpointer),
                session_factory=session_factory,
                commerce=commerce,
                audit=AuditService(session_factory),
                llm=StructuredLLM(partial(create_chat_model, settings)),
                embeddings=get_embeddings(),
                settings=settings,
                events=EventBus(),
                tracer=build_tracer(settings),
            )
            app.state.agent_runner = runner
            yield
            with suppress(TimeoutError):
                await asyncio.wait_for(runner.wait(), SHUTDOWN_GRACE_SECONDS)
        await get_engine().dispose()

    return lifespan


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the API with logging, error handlers, routes, and the agent runtime."""
    settings = settings or get_settings()
    configure_logging(settings)
    disable_ambient_tracing()
    app = FastAPI(
        title="SupportPilot AI",
        version=settings.app_version,
        lifespan=_lifespan(settings),
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    app.state.rate_limits = RateLimits.from_settings(settings)
    app.state.eval_jobs = EvalJobs(settings, get_embeddings)
    install_middleware(app, settings)
    register_exception_handlers(app)
    app.include_router(api_router, prefix="/api")
    return app


app = create_app()
