"""Shared tool plumbing: the per-ticket context and the audited LangChain tool wrapper."""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any, TypeVar

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.tools.commerce import CommerceClient
from app.services.audit_service import AuditService, AuditStatus

ArgsT = TypeVar("ArgsT", bound=BaseModel)

TOOL_CALL_ACTION = "tool_call"

OrderId = Annotated[
    str, Field(pattern=r"^ORD-\d{4,10}$", description="Order number, e.g. ORD-10001")
]
Reason = Annotated[str, Field(min_length=1, max_length=500)]


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Everything a tool needs for one ticket.

    Who the customer is and which ticket is being worked come from here, never from tool
    arguments, so a model cannot point a tool at someone else's data or ticket.
    """

    ticket_id: uuid.UUID
    customer_id: str
    session: AsyncSession
    commerce: CommerceClient
    audit: AuditService

    def idempotency_key(self, action: str) -> str:
        """Stable per ticket, so retries and repeated approvals never act twice."""
        return f"{action}-{self.ticket_id}"


def audited_tool(
    context: ToolContext,
    *,
    name: str,
    description: str,
    args_schema: type[ArgsT],
    handler: Callable[[ToolContext, ArgsT], Awaitable[BaseModel]],
) -> StructuredTool:
    """Wrap a handler as a LangChain tool that writes an audit entry for every call it runs.

    Arguments are validated against `args_schema` before the handler runs. Errors are recorded
    and then re-raised unchanged so the caller can decide whether to retry or escalate.
    """

    async def run(**kwargs: Any) -> BaseModel:
        args = args_schema.model_validate(kwargs)
        try:
            result = await handler(context, args)
        except Exception as exc:
            await context.audit.record(
                ticket_id=context.ticket_id,
                action=TOOL_CALL_ACTION,
                tool_name=name,
                status=AuditStatus.FAILURE,
                input_data=args,
                output_data={
                    "error": type(exc).__name__,
                    "code": getattr(exc, "code", None),
                    "message": str(exc),
                },
            )
            raise
        await context.audit.record(
            ticket_id=context.ticket_id,
            action=TOOL_CALL_ACTION,
            tool_name=name,
            status=AuditStatus.SUCCESS,
            input_data=args,
            output_data=result,
        )
        return result

    return StructuredTool.from_function(
        coroutine=run, name=name, description=description, args_schema=args_schema
    )
