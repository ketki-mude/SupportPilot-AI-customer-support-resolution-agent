"""Customer and order tools: the customer, their orders, one order, and cancelling an order."""

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict

from app.agents.tools.base import OrderId, Reason, ToolContext, audited_tool
from app.schemas.commerce import Cancellation, Customer, CustomerOrders, Order


class GetCustomerArgs(BaseModel):
    """No arguments: the customer is always the one who opened the ticket."""

    model_config = ConfigDict(extra="forbid")


class ListMyOrdersArgs(BaseModel):
    """No arguments: only the ticket owner's own orders can be listed."""

    model_config = ConfigDict(extra="forbid")


class GetOrderArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: OrderId


class CancelOrderArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: OrderId
    reason: Reason


async def _get_customer(context: ToolContext, _: GetCustomerArgs) -> Customer:
    return await context.commerce.get_customer(context.customer_id)


async def _list_my_orders(context: ToolContext, _: ListMyOrdersArgs) -> CustomerOrders:
    orders = await context.commerce.list_orders(context.customer_id)
    return CustomerOrders(orders=orders)


async def _get_order(context: ToolContext, args: GetOrderArgs) -> Order:
    return await context.commerce.get_order(args.order_id)


async def _cancel_order(context: ToolContext, args: CancelOrderArgs) -> Cancellation:
    return await context.commerce.cancel_order(
        order_id=args.order_id,
        reason=args.reason,
        idempotency_key=context.idempotency_key("cancel"),
    )


def order_tools(context: ToolContext) -> list[StructuredTool]:
    return [
        audited_tool(
            context,
            name="get_customer",
            description="Look up the customer who opened this ticket.",
            args_schema=GetCustomerArgs,
            handler=_get_customer,
        ),
        audited_tool(
            context,
            name="list_my_orders",
            description="List the orders of the customer who opened this ticket.",
            args_schema=ListMyOrdersArgs,
            handler=_list_my_orders,
        ),
        audited_tool(
            context,
            name="get_order",
            description="Look up an order by its order number.",
            args_schema=GetOrderArgs,
            handler=_get_order,
        ),
        audited_tool(
            context,
            name="cancel_order",
            description="Cancel an order that has not shipped; a captured payment is refunded.",
            args_schema=CancelOrderArgs,
            handler=_cancel_order,
        ),
    ]
