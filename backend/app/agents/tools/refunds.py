"""Remedy tools: refund an order or ship replacement items."""

from decimal import Decimal
from typing import Annotated

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from app.agents.tools.base import OrderId, Reason, ToolContext, audited_tool
from app.schemas.commerce import Refund, Replacement

Amount = Annotated[Decimal, Field(gt=0, max_digits=10, decimal_places=2)]


class CreateRefundArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: OrderId
    amount: Amount
    reason: Reason


class CreateReplacementArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: OrderId
    skus: list[Annotated[str, Field(min_length=1, max_length=64)]] = Field(
        default_factory=list, max_length=50, description="Items to replace; empty means all"
    )
    reason: Reason


async def _create_refund(context: ToolContext, args: CreateRefundArgs) -> Refund:
    return await context.commerce.create_refund(
        order_id=args.order_id,
        amount=args.amount,
        reason=args.reason,
        idempotency_key=context.idempotency_key("refund"),
    )


async def _create_replacement(context: ToolContext, args: CreateReplacementArgs) -> Replacement:
    return await context.commerce.create_replacement(
        order_id=args.order_id,
        skus=args.skus,
        reason=args.reason,
        idempotency_key=context.idempotency_key("replacement"),
    )


def refund_tools(context: ToolContext) -> list[StructuredTool]:
    return [
        audited_tool(
            context,
            name="create_refund",
            description="Refund an order to its original payment method.",
            args_schema=CreateRefundArgs,
            handler=_create_refund,
        ),
        audited_tool(
            context,
            name="create_replacement",
            description="Ship replacement items for a delivered order at no charge.",
            args_schema=CreateReplacementArgs,
            handler=_create_replacement,
        ),
    ]
