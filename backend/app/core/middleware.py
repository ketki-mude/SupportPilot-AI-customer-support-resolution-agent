"""Request-level hardening as plain ASGI middleware, so streaming responses are never buffered.

Outermost first: request id and access log, security headers, body size limit, timeout, CORS.
"""

import asyncio
import json
import re
import time
import uuid
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import Settings

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

logger = structlog.get_logger("app.access")

REQUEST_ID_HEADER = "x-request-id"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_STREAM_PATH = re.compile(r"/stream/?$")
_DOCS_PATHS = ("/api/docs", "/api/openapi.json")

_SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
    (b"cross-origin-opener-policy", b"same-origin"),
]
# The API only returns JSON and event streams; nothing in them may load or run anything.
_API_CSP = (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'")
_HSTS = (b"strict-transport-security", b"max-age=31536000; includeSubDomains")


async def _send_json(send: Send, status: int, code: str, message: str) -> None:
    body = json.dumps({"error": {"code": code, "message": message}}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class RequestContextMiddleware:
    """Give every request an id, bind it to every log line, and log one line per request.

    Background agent runs started during the request inherit the id, so their logs link back.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = dict(scope["headers"]).get(REQUEST_ID_HEADER.encode(), b"").decode("latin-1")
        request_id = incoming if _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        status = 500
        started = time.perf_counter()

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message.setdefault("headers", []).append(
                    (REQUEST_ID_HEADER.encode(), request_id.encode())
                )
            await send(message)

        with structlog.contextvars.bound_contextvars(request_id=request_id):
            try:
                await self.app(scope, receive, send_with_id)
            finally:
                logger.info(
                    "request_completed",
                    method=scope["method"],
                    path=scope["path"],
                    status=status,
                    duration_ms=round((time.perf_counter() - started) * 1000, 1),
                )


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, *, hsts: bool) -> None:
        self.app = app
        self.hsts = hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        # Swagger UI needs its own scripts and styles, so the docs pages skip the strict CSP.
        docs = scope["path"].startswith(_DOCS_PATHS)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                headers.extend(_SECURITY_HEADERS)
                if not docs:
                    headers.append(_API_CSP)
                if self.hsts:
                    headers.append(_HSTS)
            await send(message)

        await self.app(scope, receive, send_with_headers)


class BodySizeLimitMiddleware:
    """Refuse request bodies over the limit, whether or not Content-Length is sent."""

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
            await _too_large(send, self.max_bytes)
            return

        received = 0
        too_large = False
        answered = False

        async def limited_receive() -> Message:
            nonlocal received, too_large
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    too_large = True
                    raise _BodyTooLargeError
            return message

        async def guarded_send(message: Message) -> None:
            # FastAPI turns a failed body read into its own 400; answer 413 instead.
            nonlocal answered
            if too_large:
                if not answered:
                    answered = True
                    await _too_large(send, self.max_bytes)
                return
            answered = answered or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except _BodyTooLargeError:
            if not answered:
                await _too_large(send, self.max_bytes)


class _BodyTooLargeError(Exception):
    pass


async def _too_large(send: Send, max_bytes: int) -> None:
    await _send_json(
        send, 413, "payload_too_large", f"Request bodies are limited to {max_bytes} bytes"
    )


class RequestTimeoutMiddleware:
    """Bound every request except the long-lived event stream."""

    def __init__(self, app: ASGIApp, *, seconds: float) -> None:
        self.app = app
        self.seconds = seconds

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or _STREAM_PATH.search(scope["path"]):
            await self.app(scope, receive, send)
            return
        started = False

        async def tracking_send(message: Message) -> None:
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            async with asyncio.timeout(self.seconds):
                await self.app(scope, receive, tracking_send)
        except TimeoutError:
            logger.warning("request_timed_out", path=scope["path"], seconds=self.seconds)
            if not started:
                await _send_json(send, 504, "request_timeout", "The request took too long")


def install_middleware(app: FastAPI, settings: Settings) -> None:
    """Register in reverse: Starlette runs the last one added first."""
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,  # the refresh cookie
            allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "Content-Type", "Last-Event-ID", "X-Request-ID"],
            expose_headers=["X-Request-ID", "Retry-After"],
            max_age=600,
        )
    app.add_middleware(RequestTimeoutMiddleware, seconds=settings.request_timeout_seconds)
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_bytes)
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.app_env == "production")
    app.add_middleware(RequestContextMiddleware)
