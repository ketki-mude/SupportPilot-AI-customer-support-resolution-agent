"""Acme mock commerce API: customers, orders, and idempotent refunds and replacements.

State is held in memory and resets when the service restarts. Seed dates are relative to start
time so edge cases (for example "delivered 45 days ago") stay valid. MOCK_FAILURE_RATE injects
random 503s and slow responses so callers can exercise retries and timeouts.
"""

import asyncio
import hashlib
import json
import random
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from http import HTTPStatus
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import FastAPI, Header, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.exceptions import HTTPException as StarletteHTTPException

SEED_PATH = Path(__file__).with_name("seed_data.json")
FAULT_EXEMPT_PATHS = frozenset({"/health", "/docs", "/openapi.json"})

Money = Annotated[Decimal, Field(max_digits=10, decimal_places=2)]
PositiveMoney = Annotated[Decimal, Field(gt=0, max_digits=10, decimal_places=2)]
IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key", max_length=255)]


class MockSettings(BaseSettings):
    """Fault injection knobs, read from MOCK_* environment variables."""

    model_config = SettingsConfigDict(env_prefix="MOCK_", env_ignore_empty=True, extra="ignore")

    failure_rate: float = Field(default=0.0, ge=0, le=1, description="Share of requests faulted")
    slow_fraction: float = Field(
        default=0.5, ge=0, le=1, description="Share of faults that are slow"
    )
    slow_seconds: float = Field(default=2.0, ge=0, le=30)
    random_seed: int | None = None


class OrderStatus(StrEnum):
    PROCESSING = "processing"
    IN_TRANSIT = "in_transit"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class RefundStatus(StrEnum):
    NONE = "none"
    PARTIAL = "partial"
    FULL = "full"


class Customer(BaseModel):
    id: str
    name: str
    email: str
    phone: str
    tier: Literal["standard", "gold", "platinum"]
    fraud_flag: bool
    member_since: datetime


class OrderItem(BaseModel):
    sku: str
    name: str
    quantity: int = Field(ge=1)
    unit_price: Money


class Order(BaseModel):
    order_id: str
    customer_id: str
    status: OrderStatus
    items: list[OrderItem]
    total: Money
    currency: Literal["USD"] = "USD"
    placed_at: datetime
    shipped_at: datetime | None
    delivered_at: datetime | None
    estimated_delivery_at: datetime | None
    tracking_number: str | None
    payment_method_last4: str
    refunded_amount: Money = Decimal("0.00")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def refund_status(self) -> RefundStatus:
        """Whether any, or all, of the order total has been refunded."""
        if self.refunded_amount == 0:
            return RefundStatus.NONE
        return RefundStatus.FULL if self.refunded_amount >= self.total else RefundStatus.PARTIAL

    def refundable_amount(self) -> Decimal:
        """Money still available to refund. Cancelled orders were never charged."""
        if self.status == OrderStatus.CANCELLED:
            return Decimal("0.00")
        return self.total - self.refunded_amount


class RefundRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: str = Field(min_length=1, max_length=64)
    amount: PositiveMoney
    reason: str = Field(min_length=1, max_length=500)


class Refund(BaseModel):
    refund_id: str
    order_id: str
    amount: Money
    reason: str
    status: Literal["succeeded"] = "succeeded"
    created_at: datetime


class ReplacementRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=500)
    skus: list[str] | None = Field(default=None, min_length=1, description="Defaults to all items")


class Replacement(BaseModel):
    replacement_id: str
    order_id: str
    items: list[OrderItem]
    reason: str
    status: Literal["created"] = "created"
    created_at: datetime


class MockError(Exception):
    """An error rendered as the standard {"error": {"code", "message"}} envelope."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    fingerprint: str
    body: dict[str, Any]


class CommerceStore:
    """In-memory customers, orders, refunds, replacements, and idempotency records."""

    def __init__(self, customers: dict[str, Customer], orders: dict[str, Order]) -> None:
        self.customers = customers
        self.orders = orders
        self.refunds: dict[str, Refund] = {}
        self.replacements: dict[str, Replacement] = {}
        self.idempotency: dict[str, IdempotencyRecord] = {}
        self.lock = asyncio.Lock()

    def get_customer(self, customer_id: str) -> Customer:
        customer = self.customers.get(customer_id)
        if customer is None:
            raise MockError(HTTPStatus.NOT_FOUND, "customer_not_found", "Customer not found")
        return customer

    def get_order(self, order_id: str) -> Order:
        order = self.orders.get(order_id)
        if order is None:
            raise MockError(HTTPStatus.NOT_FOUND, "order_not_found", "Order not found")
        return order

    def orders_for(self, customer_id: str) -> list[Order]:
        self.get_customer(customer_id)
        return [o for o in self.orders.values() if o.customer_id == customer_id]

    def record_refund(self, refund: Refund) -> None:
        self.refunds[refund.refund_id] = refund
        self.orders[refund.order_id].refunded_amount += refund.amount

    def create_refund(self, request: RefundRequest) -> Refund:
        """Refund part or all of an order, never more than was charged."""
        order = self.get_order(request.order_id)
        refundable = order.refundable_amount()
        if request.amount > refundable:
            raise MockError(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                "refund_exceeds_balance",
                f"Refund of {request.amount} exceeds the refundable balance of {refundable}",
            )
        refund = Refund(
            refund_id=f"re_{secrets.token_hex(8)}",
            order_id=order.order_id,
            amount=request.amount,
            reason=request.reason,
            created_at=datetime.now(UTC),
        )
        self.record_refund(refund)
        return refund

    def create_replacement(self, request: ReplacementRequest) -> Replacement:
        """Ship replacement items for a delivered order."""
        order = self.get_order(request.order_id)
        if order.status != OrderStatus.DELIVERED:
            raise MockError(
                HTTPStatus.CONFLICT, "order_not_delivered", "Only delivered orders can be replaced"
            )
        items = order.items
        if request.skus is not None:
            unknown = set(request.skus) - {item.sku for item in order.items}
            if unknown:
                raise MockError(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    "unknown_sku",
                    f"Not part of this order: {', '.join(sorted(unknown))}",
                )
            items = [item for item in order.items if item.sku in request.skus]
        replacement = Replacement(
            replacement_id=f"rp_{secrets.token_hex(8)}",
            order_id=order.order_id,
            items=items,
            reason=request.reason,
            created_at=datetime.now(UTC),
        )
        self.replacements[replacement.replacement_id] = replacement
        return replacement


def _days_ago(now: datetime, days: int | None) -> datetime | None:
    return None if days is None else now - timedelta(days=days)


def load_store(path: Path, now: datetime) -> CommerceStore:
    """Build the store from seed JSON, turning relative day offsets into timestamps."""
    data = json.loads(path.read_text())
    customers = {
        raw["id"]: Customer(
            id=raw["id"],
            name=raw["name"],
            email=raw["email"],
            phone=raw["phone"],
            tier=raw["tier"],
            fraud_flag=raw["fraud_flag"],
            member_since=now - timedelta(days=raw["member_since_days_ago"]),
        )
        for raw in data["customers"]
    }
    orders: dict[str, Order] = {}
    for raw in data["orders"]:
        items = [OrderItem(**item) for item in raw["items"]]
        eta_days = raw.get("estimated_delivery_in_days")
        orders[raw["order_id"]] = Order(
            order_id=raw["order_id"],
            customer_id=raw["customer_id"],
            status=raw["status"],
            items=items,
            total=sum((item.unit_price * item.quantity for item in items), Decimal("0.00")),
            placed_at=now - timedelta(days=raw["placed_days_ago"]),
            shipped_at=_days_ago(now, raw.get("shipped_days_ago")),
            delivered_at=_days_ago(now, raw.get("delivered_days_ago")),
            estimated_delivery_at=None if eta_days is None else now + timedelta(days=eta_days),
            tracking_number=raw.get("tracking_number"),
            payment_method_last4=raw["payment_method_last4"],
        )
    store = CommerceStore(customers, orders)
    for raw in data["prior_refunds"]:
        store.record_refund(
            Refund(
                refund_id=raw["refund_id"],
                order_id=raw["order_id"],
                amount=Decimal(raw["amount"]),
                reason=raw["reason"],
                created_at=now - timedelta(days=raw["days_ago"]),
            )
        )
    return store


def _error(
    status_code: int, code: str, message: str, headers: dict[str, str] | None = None
) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message}}, status_code=status_code, headers=headers
    )


async def _run_idempotent(
    store: CommerceStore,
    scope: str,
    key: str | None,
    request: BaseModel,
    action: Callable[[], BaseModel],
) -> JSONResponse:
    """Run `action` once per key. Replays return the stored result; a changed body is a 409."""
    if not key:
        raise MockError(
            HTTPStatus.BAD_REQUEST,
            "idempotency_key_required",
            "The Idempotency-Key header is required",
        )
    fingerprint = hashlib.sha256(request.model_dump_json().encode()).hexdigest()
    record_key = f"{scope}:{key}"
    async with store.lock:
        record = store.idempotency.get(record_key)
        if record is not None:
            if record.fingerprint != fingerprint:
                raise MockError(
                    HTTPStatus.CONFLICT,
                    "idempotency_key_reused",
                    "This Idempotency-Key was already used with a different request body",
                )
            return JSONResponse(
                record.body, status_code=HTTPStatus.CREATED, headers={"Idempotent-Replayed": "true"}
            )
        body = action().model_dump(mode="json")
        store.idempotency[record_key] = IdempotencyRecord(fingerprint, body)
    return JSONResponse(body, status_code=HTTPStatus.CREATED)


def _install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(MockError)
    async def handle_mock_error(_: Request, exc: MockError) -> JSONResponse:
        return _error(exc.status_code, exc.code, exc.message)

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        status = HTTPStatus(exc.status_code)
        message = exc.detail if isinstance(exc.detail, str) else status.phrase
        return _error(exc.status_code, status.phrase.lower().replace(" ", "_"), message)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first["loc"])
        return _error(
            HTTPStatus.UNPROCESSABLE_ENTITY, "validation_error", f"{location}: {first['msg']}"
        )


def _install_fault_injection(app: FastAPI, settings: MockSettings) -> None:
    rng = random.Random(settings.random_seed)  # noqa: S311  # simulation, not cryptography

    @app.middleware("http")
    async def inject_faults(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path not in FAULT_EXEMPT_PATHS and rng.random() < settings.failure_rate:
            if rng.random() >= settings.slow_fraction:
                return _error(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    "service_unavailable",
                    "Injected failure: the commerce service is temporarily unavailable",
                    headers={"Retry-After": "1"},
                )
            await asyncio.sleep(settings.slow_seconds)
        return await call_next(request)


def create_app(settings: MockSettings | None = None, store: CommerceStore | None = None) -> FastAPI:
    """Build the mock API. Tests pass their own settings and store."""
    settings = settings or MockSettings()
    commerce = store or load_store(SEED_PATH, datetime.now(UTC))
    app = FastAPI(title="Acme mock commerce API", version="1.0.0")
    _install_error_handlers(app)
    _install_fault_injection(app, settings)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/customers/{customer_id}")
    async def get_customer(customer_id: str) -> Customer:
        return commerce.get_customer(customer_id)

    @app.get("/customers/{customer_id}/orders")
    async def list_customer_orders(customer_id: str) -> list[Order]:
        return commerce.orders_for(customer_id)

    @app.get("/orders/{order_id}")
    async def get_order(order_id: str) -> Order:
        return commerce.get_order(order_id)

    @app.post("/refunds", status_code=HTTPStatus.CREATED, response_model=Refund)
    async def create_refund(
        body: RefundRequest, idempotency_key: IdempotencyKey = None
    ) -> Response:
        return await _run_idempotent(
            commerce, "refunds", idempotency_key, body, lambda: commerce.create_refund(body)
        )

    @app.post("/replacements", status_code=HTTPStatus.CREATED, response_model=Replacement)
    async def create_replacement(
        body: ReplacementRequest, idempotency_key: IdempotencyKey = None
    ) -> Response:
        return await _run_idempotent(
            commerce,
            "replacements",
            idempotency_key,
            body,
            lambda: commerce.create_replacement(body),
        )

    return app


app = create_app()
