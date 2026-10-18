"""Shared tool plumbing: the per-ticket context and the audited LangChain tool wrapper."""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any, TypeVar

from langchain_core.messages import ToolCall
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.guardrails import redact_value
from app.agents.tools.commerce import CommerceClient
from app.services.audit_service import AGENT_ACTOR, AuditService, AuditStatus

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
    # Who the audit log names for each call: the agent, or the staff member acting by hand.
    actor: str = AGENT_ACTOR

    def idempotency_key(self, action: str) -> str:
        """Stable per ticket, so retries and repeated approvals never act twice."""
        return f"{action}-{self.ticket_id}"


RejectHook = Callable[[Any, ValidationError], Awaitable[None]]


class AuditedTool(StructuredTool):
    """A StructuredTool that also audits calls whose arguments fail validation.

    Every attempted call leaves a trace, including ones blocked before they run: those are the
    calls a manipulated model or a buggy step would make.
    """

    validator: type[BaseModel]
    on_rejected: RejectHook

    async def ainvoke(
        self,
        input: str | dict[str, Any] | ToolCall,
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> Any:
        raw: Any = input
        if isinstance(input, dict) and input.get("type") == "tool_call":
            raw = input.get("args", {})
        try:
            self.validator.model_validate(raw)
        except ValidationError as exc:
            await self.on_rejected(raw, exc)
            raise
        return await super().ainvoke(input, config, **kwargs)


def audited_tool(
    context: ToolContext,
    *,
    name: str,
    description: str,
    args_schema: type[ArgsT],
    handler: Callable[[ToolContext, ArgsT], Awaitable[BaseModel]],
) -> StructuredTool:
    """Wrap a handler as a LangChain tool that writes an audit entry for every call attempted.

    Arguments are validated against `args_schema` first; a rejected call is audited as
    `rejected_invalid` with redacted arguments. Handler errors are recorded and re-raised
    unchanged so the caller can decide whether to retry or escalate.
    """

    async def on_rejected(raw: Any, error: ValidationError) -> None:
        await context.audit.record(
            ticket_id=context.ticket_id,
            actor=context.actor,
            action=TOOL_CALL_ACTION,
            tool_name=name,
            status=AuditStatus.REJECTED_INVALID,
            input_data=redact_value(raw) if isinstance(raw, dict) else {"value": redact_value(raw)},
            output_data={
                "error": "validation_error",
                "issues": [
                    {"field": ".".join(str(p) for p in issue["loc"]), "message": issue["msg"]}
                    for issue in error.errors()
                ],
            },
        )

    async def run(**kwargs: Any) -> BaseModel:
        args = args_schema.model_validate(kwargs)
        try:
            result = await handler(context, args)
        except Exception as exc:
            await context.audit.record(
                ticket_id=context.ticket_id,
                actor=context.actor,
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
            actor=context.actor,
            action=TOOL_CALL_ACTION,
            tool_name=name,
            status=AuditStatus.SUCCESS,
            input_data=args,
            output_data=result,
        )
        return result

    return AuditedTool(
        name=name,
        description=description,
        args_schema=args_schema,
        coroutine=run,
        validator=args_schema,
        on_rejected=on_rejected,
    )
