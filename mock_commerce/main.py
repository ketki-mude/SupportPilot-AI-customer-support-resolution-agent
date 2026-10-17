"""Acme mock commerce API: customers, orders, and idempotent refunds, replacements, cancellations.

State is held in memory and resets when the service restarts. Seed dates are relative to start
time so edge cases (for example "delivered 45 days ago") stay valid. MOCK_FAILURE_RATE injects
random 503s and slow responses so callers can exercise retries and timeouts.

Each order takes at most one remedy (a refund or a replacement). Cancelling a processing order
refunds a captured payment, or voids an authorized one, in the same call.
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
ADMIN_PREFIX = "/admin/"

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
    # Evaluation instances only: lets the harness load each case's data and faults.
    admin_enabled: bool = False


class Faults(BaseModel):
    """Fault injection for one run of the shop; replaceable through the admin API."""

    model_config = ConfigDict(extra="forbid")

    failure_rate: float = Field(default=0.0, ge=0, le=1)
    slow_fraction: float = Field(default=0.5, ge=0, le=1)
    slow_seconds: float = Field(default=2.0, ge=0, le=30)
    random_seed: int | None = None


class OrderStatus(StrEnum):
    PROCESSING = "processing"
    SHIPPED = "shipped"
    IN_TRANSIT = "in_transit"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class PaymentStatus(StrEnum):
    AUTHORIZED = "authorized"
    CAPTURED = "captured"
    VOIDED = "voided"


class Remedy(StrEnum):
    REFUND = "refund"
    REPLACEMENT = "replacement"


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
    payment_status: PaymentStatus
    items: list[OrderItem]
    total: Money
    currency: Literal["USD"] = "USD"
    placed_at: datetime
    shipped_at: datetime | None
    delivered_at: datetime | None
    estimated_delivery_at: datetime | None
    cancelled_at: datetime | None = None
    tracking_number: str | None
    payment_method_last4: str
    refunded_amount: Money = Decimal("0.00")
    remedy: Remedy | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def refund_status(self) -> RefundStatus:
        """Whether any, or all, of the order total has been refunded."""
        if self.refunded_amount == 0:
            return RefundStatus.NONE
        return RefundStatus.FULL if self.refunded_amount >= self.total else RefundStatus.PARTIAL

    def refundable_amount(self) -> Decimal:
        """Money still available to refund. Only a captured payment can be refunded."""
        if self.payment_status != PaymentStatus.CAPTURED:
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


class CancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=500)


class CancelFingerprint(BaseModel):
    """What makes two cancel requests "the same" for idempotency: the order and the reason."""

    order_id: str
    reason: str


class Cancellation(BaseModel):
    order_id: str
    status: Literal["cancelled"] = "cancelled"
    payment_action: Literal["refunded", "voided"]
    refund: Refund | None
    cancelled_at: datetime


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

    def record_refund(self, refund: Refund, *, as_remedy: bool) -> None:
        """Store a refund. Refunds that come from a cancellation are not a remedy."""
        order = self.orders[refund.order_id]
        self.refunds[refund.refund_id] = refund
        order.refunded_amount += refund.amount
        if as_remedy:
            order.remedy = Remedy.REFUND

    def record_replacement(self, replacement: Replacement) -> None:
        self.replacements[replacement.replacement_id] = replacement
        self.orders[replacement.order_id].remedy = Remedy.REPLACEMENT

    @staticmethod
    def _ensure_no_remedy(order: Order) -> None:
        if order.remedy is not None:
            raise MockError(
                HTTPStatus.CONFLICT,
                "remedy_already_applied",
                f"Order {order.order_id} already has a {order.remedy.value}",
            )

    def create_refund(self, request: RefundRequest) -> Refund:
        """Refund part or all of an order once, never more than was charged."""
        order = self.get_order(request.order_id)
        self._ensure_no_remedy(order)
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
        self.record_refund(refund, as_remedy=True)
        return refund

    def create_replacement(self, request: ReplacementRequest) -> Replacement:
        """Ship replacement items for a delivered order."""
        order = self.get_order(request.order_id)
        if order.status != OrderStatus.DELIVERED:
            raise MockError(
                HTTPStatus.CONFLICT, "order_not_delivered", "Only delivered orders can be replaced"
            )
        self._ensure_no_remedy(order)
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
        self.record_replacement(replacement)
        return replacement

    def cancel_order(self, order_id: str, reason: str) -> Cancellation:
        """Cancel a processing order, refunding a captured payment or voiding an authorized one."""
        order = self.get_order(order_id)
        if order.status != OrderStatus.PROCESSING:
            raise MockError(
                HTTPStatus.CONFLICT,
                "order_not_cancellable",
                f"Only processing orders can be cancelled; this order is {order.status.value}",
            )
        now = datetime.now(UTC)
        refund: Refund | None = None
        if order.payment_status == PaymentStatus.CAPTURED:
            refund = Refund(
                refund_id=f"re_{secrets.token_hex(8)}",
                order_id=order.order_id,
                amount=order.refundable_amount(),
                reason=f"Order cancelled: {reason}",
                created_at=now,
            )
            self.record_refund(refund, as_remedy=False)
        else:
            order.payment_status = PaymentStatus.VOIDED
        order.status = OrderStatus.CANCELLED
        order.cancelled_at = now
        return Cancellation(
            order_id=order.order_id,
            payment_action="refunded" if refund else "voided",
            refund=refund,
            cancelled_at=now,
        )


def _days_ago(now: datetime, days: int | None) -> datetime | None:
    return None if days is None else now - timedelta(days=days)


def load_store(path: Path, now: datetime) -> CommerceStore:
    """Build the store from a seed file."""
    return build_store(json.loads(path.read_text()), now)


def build_store(data: dict[str, Any], now: datetime) -> CommerceStore:
    """Build the store from seed data, turning relative day offsets into timestamps."""
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
            payment_status=raw.get("payment_status", PaymentStatus.CAPTURED),
            items=items,
            total=sum((item.unit_price * item.quantity for item in items), Decimal("0.00")),
            placed_at=now - timedelta(days=raw["placed_days_ago"]),
            shipped_at=_days_ago(now, raw.get("shipped_days_ago")),
            delivered_at=_days_ago(now, raw.get("delivered_days_ago")),
            estimated_delivery_at=None if eta_days is None else now + timedelta(days=eta_days),
            cancelled_at=_days_ago(now, raw.get("cancelled_days_ago")),
            tracking_number=raw.get("tracking_number"),
            payment_method_last4=raw["payment_method_last4"],
        )
    store = CommerceStore(customers, orders)
    for raw in data.get("prior_refunds", []):
        store.record_refund(
            Refund(
                refund_id=raw["refund_id"],
                order_id=raw["order_id"],
                amount=Decimal(raw["amount"]),
                reason=raw["reason"],
                created_at=now - timedelta(days=raw["days_ago"]),
            ),
            as_remedy=True,
        )
    for raw in data.get("prior_replacements", []):
        store.record_replacement(
            Replacement(
                replacement_id=raw["replacement_id"],
                order_id=raw["order_id"],
                items=orders[raw["order_id"]].items,
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


@dataclass
class Runtime:
    """The shop's current data and faults. The admin API swaps both between eval cases."""

    store: CommerceStore
    faults: Faults
    rng: random.Random

    @classmethod
    def create(cls, store: CommerceStore, faults: Faults) -> "Runtime":
        return cls(store, faults, random.Random(faults.random_seed))  # noqa: S311  # simulation

    def reset(self, store: CommerceStore, faults: Faults) -> None:
        self.store = store
        self.faults = faults
        self.rng = random.Random(faults.random_seed)  # noqa: S311  # simulation


class ResetRequest(BaseModel):
    """A case's data in seed-file format, plus the faults to apply while it runs."""

    model_config = ConfigDict(extra="forbid")

    fixture: dict[str, Any]
    faults: Faults = Field(default_factory=lambda: Faults(failure_rate=0.0))


def _faulted(path: str) -> bool:
    return path not in FAULT_EXEMPT_PATHS and not path.startswith(ADMIN_PREFIX)


def _install_fault_injection(app: FastAPI, runtime: Runtime) -> None:
    @app.middleware("http")
    async def inject_faults(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        faults, rng = runtime.faults, runtime.rng
        if _faulted(request.url.path) and rng.random() < faults.failure_rate:
            if rng.random() >= faults.slow_fraction:
                return _error(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    "service_unavailable",
                    "Injected failure: the commerce service is temporarily unavailable",
                    headers={"Retry-After": "1"},
                )
            await asyncio.sleep(faults.slow_seconds)
        return await call_next(request)


def create_app(settings: MockSettings | None = None, store: CommerceStore | None = None) -> FastAPI:
    """Build the mock API. Tests pass their own settings and store."""
    settings = settings or MockSettings()
    runtime = Runtime.create(
        store or load_store(SEED_PATH, datetime.now(UTC)),
        Faults(
            failure_rate=settings.failure_rate,
            slow_fraction=settings.slow_fraction,
            slow_seconds=settings.slow_seconds,
            random_seed=settings.random_seed,
        ),
    )
    app = FastAPI(title="Acme mock commerce API", version="1.0.0")
    _install_error_handlers(app)
    _install_fault_injection(app, runtime)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/customers/{customer_id}")
    async def get_customer(customer_id: str) -> Customer:
        return runtime.store.get_customer(customer_id)

    @app.get("/customers/{customer_id}/orders")
    async def list_customer_orders(customer_id: str) -> list[Order]:
        return runtime.store.orders_for(customer_id)

    @app.get("/orders/{order_id}")
    async def get_order(order_id: str) -> Order:
        return runtime.store.get_order(order_id)

    @app.post("/refunds", status_code=HTTPStatus.CREATED, response_model=Refund)
    async def create_refund(
        body: RefundRequest, idempotency_key: IdempotencyKey = None
    ) -> Response:
        return await _run_idempotent(
            runtime.store,
            "refunds",
            idempotency_key,
            body,
            lambda: runtime.store.create_refund(body),
        )

    @app.post("/replacements", status_code=HTTPStatus.CREATED, response_model=Replacement)
    async def create_replacement(
        body: ReplacementRequest, idempotency_key: IdempotencyKey = None
    ) -> Response:
        return await _run_idempotent(
            runtime.store,
            "replacements",
            idempotency_key,
            body,
            lambda: runtime.store.create_replacement(body),
        )

    @app.post(
        "/orders/{order_id}/cancel", status_code=HTTPStatus.CREATED, response_model=Cancellation
    )
    async def cancel_order(
        order_id: str, body: CancelRequest, idempotency_key: IdempotencyKey = None
    ) -> Response:
        return await _run_idempotent(
            runtime.store,
            "cancellations",
            idempotency_key,
            CancelFingerprint(order_id=order_id, reason=body.reason),
            lambda: runtime.store.cancel_order(order_id, body.reason),
        )

    if settings.admin_enabled:
        _install_admin(app, runtime)
    return app


def _install_admin(app: FastAPI, runtime: Runtime) -> None:
    """Evaluation-only endpoints: load a case's data and read back what the agent did."""

    @app.post("/admin/reset", status_code=HTTPStatus.NO_CONTENT)
    async def reset(body: ResetRequest) -> Response:
        async with runtime.store.lock:
            runtime.reset(build_store(body.fixture, datetime.now(UTC)), body.faults)
        return Response(status_code=HTTPStatus.NO_CONTENT)

    @app.get("/admin/state")
    async def state() -> dict[str, Any]:
        store = runtime.store
        return {
            "refunds": [r.model_dump(mode="json") for r in store.refunds.values()],
            "replacements": [r.model_dump(mode="json") for r in store.replacements.values()],
            "orders": [o.model_dump(mode="json") for o in store.orders.values()],
        }


app = create_app()
