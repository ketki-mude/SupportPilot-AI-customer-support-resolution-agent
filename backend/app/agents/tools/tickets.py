"""Ticket tool: update the ticket being worked."""

from langchain_core.tools import StructuredTool

from app.agents.tools.base import ToolContext, audited_tool
from app.schemas.ticket import TicketRead, TicketUpdate
from app.services import ticket_service


async def _update_ticket(context: ToolContext, args: TicketUpdate) -> TicketRead:
    ticket = await ticket_service.get_ticket(context.session, context.ticket_id)
    updated = await ticket_service.update_ticket(context.session, ticket, args)
    return TicketRead.model_validate(updated)


def ticket_tools(context: ToolContext) -> list[StructuredTool]:
    return [
        audited_tool(
            context,
            name="update_ticket",
            description="Change this ticket's status, category, priority, summary, or order.",
            args_schema=TicketUpdate,
            handler=_update_ticket,
        )
    ]
