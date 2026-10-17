"""Notification tool: message the customer on their ticket (the only channel in scope)."""

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from app.agents.tools.base import ToolContext, audited_tool
from app.db.models import MessageSender
from app.schemas.ticket import MessageRead
from app.services import ticket_service


class NotifyCustomerArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=4000)


async def _notify_customer(context: ToolContext, args: NotifyCustomerArgs) -> MessageRead:
    message = await ticket_service.add_message(
        context.session, context.ticket_id, MessageSender.AGENT, args.message
    )
    return MessageRead.model_validate(message)


def notification_tools(context: ToolContext) -> list[StructuredTool]:
    return [
        audited_tool(
            context,
            name="notify_customer",
            description="Send the customer a message on this ticket.",
            args_schema=NotifyCustomerArgs,
            handler=_notify_customer,
        )
    ]
