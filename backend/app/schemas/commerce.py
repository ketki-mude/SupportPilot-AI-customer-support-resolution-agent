"""The backend's typed view of the Acme commerce API responses."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict


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


class CommerceModel(BaseModel):
    """Ignore fields we do not use, so additive API changes never break the client."""

    model_config = ConfigDict(extra="ignore", frozen=True)


class Customer(CommerceModel):
    id: str
    name: str
    email: str
    phone: str
    tier: Literal["standard", "gold", "platinum"]
    fraud_flag: bool
    member_since: datetime


class OrderItem(CommerceModel):
    sku: str
    name: str
    quantity: int
    unit_price: Decimal

    @property
    def line_total(self) -> Decimal:
        return self.unit_price * self.quantity


class Order(CommerceModel):
    order_id: str
    customer_id: str
    status: OrderStatus
    payment_status: PaymentStatus
    items: list[OrderItem]
    total: Decimal
    currency: Literal["USD"]
    placed_at: datetime
    shipped_at: datetime | None
    delivered_at: datetime | None
    estimated_delivery_at: datetime | None
    cancelled_at: datetime | None
    tracking_number: str | None
    payment_method_last4: str
    refunded_amount: Decimal
    remedy: Remedy | None


class CustomerOrders(CommerceModel):
    orders: list[Order]

    @property
    def ids(self) -> set[str]:
        return {order.order_id for order in self.orders}


class Refund(CommerceModel):
    refund_id: str
    order_id: str
    amount: Decimal
    reason: str
    status: Literal["succeeded"]
    created_at: datetime


class Replacement(CommerceModel):
    replacement_id: str
    order_id: str
    items: list[OrderItem]
    reason: str
    status: Literal["created"]
    created_at: datetime


class Cancellation(CommerceModel):
    order_id: str
    status: Literal["cancelled"]
    payment_action: Literal["refunded", "voided"]
    refund: Refund | None
    cancelled_at: datetime
