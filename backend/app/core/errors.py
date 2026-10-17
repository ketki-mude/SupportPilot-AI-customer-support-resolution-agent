"""Application errors and handlers that render every failure as {"error": {...}}."""

from collections.abc import Mapping
from http import HTTPStatus
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = structlog.get_logger(__name__)


class AppError(Exception):
    """Base for errors that map to a specific HTTP status and error code."""

    status_code: int = HTTPStatus.BAD_REQUEST
    code: str = "bad_request"
    headers: dict[str, str] | None = None

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code


class UnauthorizedError(AppError):
    """The request has no valid credentials."""

    status_code = HTTPStatus.UNAUTHORIZED
    code = "unauthorized"
    headers = {"WWW-Authenticate": "Bearer"}  # noqa: RUF012  # read-only class constant


class ForbiddenError(AppError):
    """The caller is authenticated but lacks the required role."""

    status_code = HTTPStatus.FORBIDDEN
    code = "forbidden"


class NotFoundError(AppError):
    """The requested resource does not exist or is not visible to the caller."""

    status_code = HTTPStatus.NOT_FOUND
    code = "not_found"


class ServiceUnavailableError(AppError):
    """A dependency the request needs is not running."""

    status_code = HTTPStatus.SERVICE_UNAVAILABLE
    code = "service_unavailable"


class ConflictError(AppError):
    """The request conflicts with the current state of the resource."""

    status_code = HTTPStatus.CONFLICT
    code = "conflict"


def error_response(
    status_code: int,
    code: str,
    message: str,
    *,
    details: list[dict[str, Any]] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """Build the standard error envelope."""
    body: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        body["details"] = details
    return JSONResponse({"error": body}, status_code=status_code, headers=headers)


def register_exception_handlers(app: FastAPI) -> None:
    """Attach the error envelope handlers to an app."""

    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        return error_response(exc.status_code, exc.code, exc.message, headers=exc.headers)

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        status = HTTPStatus(exc.status_code)
        message = exc.detail if isinstance(exc.detail, str) else status.phrase
        code = status.phrase.lower().replace(" ", "_").replace("-", "_")
        return error_response(exc.status_code, code, message, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Field locations and messages only: echoing "input" back could leak passwords.
        details = [
            {"loc": list(err["loc"]), "message": err["msg"], "type": err["type"]}
            for err in exc.errors()
        ]
        return error_response(
            HTTPStatus.UNPROCESSABLE_ENTITY,
            "validation_error",
            "Request validation failed",
            details=details,
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, _: Exception) -> JSONResponse:
        logger.exception("unhandled_error", path=request.url.path, method=request.method)
        return error_response(
            HTTPStatus.INTERNAL_SERVER_ERROR, "internal_error", "An unexpected error occurred"
        )
