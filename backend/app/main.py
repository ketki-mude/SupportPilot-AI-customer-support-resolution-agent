"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import api_router
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.db.session import get_engine


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Release database connections on shutdown."""
    yield
    await get_engine().dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the API with logging, error handlers, and routes configured."""
    settings = settings or get_settings()
    configure_logging(settings)
    app = FastAPI(
        title="SupportPilot AI",
        version=settings.app_version,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    register_exception_handlers(app)
    app.include_router(api_router, prefix="/api")
    return app


app = create_app()
