"""The agent's tools, each bound to one ticket's context."""

from langchain_core.tools import StructuredTool

from app.agents.tools.base import ToolContext
from app.agents.tools.notifications import notification_tools
from app.agents.tools.orders import order_tools
from app.agents.tools.refunds import refund_tools
from app.agents.tools.tickets import ticket_tools


def build_tools(context: ToolContext) -> dict[str, StructuredTool]:
    """Every tool for this ticket, keyed by name."""
    tools = [
        *order_tools(context),
        *refund_tools(context),
        *ticket_tools(context),
        *notification_tools(context),
    ]
    return {tool.name: tool for tool in tools}
