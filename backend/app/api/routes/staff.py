"""Staff handling a ticket by hand: reply, check an action against the rules, act, or close."""

import uuid
from http import HTTPStatus

from fastapi import APIRouter

from app.api.deps import AgentRunnerDep, SessionDep, SettingsDep, StaffUser
from app.schemas.errors import ErrorResponse
from app.schemas.staff import (
    ActionCheck,
    StaffActionCheckRequest,
    StaffActionRequest,
    StaffActionResult,
    StaffCloseRequest,
    StaffReplyRequest,
)
from app.schemas.ticket import MessageRead, TicketRead
from app.services import staff_service

router = APIRouter(
    prefix="/tickets",
    tags=["staff"],
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}},
)


@router.post(
    "/{ticket_id}/reply", status_code=HTTPStatus.CREATED, responses={404: {"model": ErrorResponse}}
)
async def reply(
    ticket_id: uuid.UUID,
    body: StaffReplyRequest,
    user: StaffUser,
    session: SessionDep,
    runner: AgentRunnerDep,
) -> MessageRead:
    """Message the customer as the support team. Shows up in their chat right away."""
    message = await staff_service.reply(
        session, ticket_id, user, body.message, audit=runner.audit, events=runner.events
    )
    return MessageRead.model_validate(message)


@router.post(
    "/{ticket_id}/actions/check",
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def check_action(
    ticket_id: uuid.UUID,
    body: StaffActionCheckRequest,
    _: StaffUser,
    session: SessionDep,
    runner: AgentRunnerDep,
    settings: SettingsDep,
) -> ActionCheck:
    """What the business rules say about an action: allowed, an override, or blocked."""
    return await staff_service.check(
        session, ticket_id, body, commerce=runner.commerce, settings=settings
    )


@router.post(
    "/{ticket_id}/actions",
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def take_action(
    ticket_id: uuid.UUID,
    body: StaffActionRequest,
    user: StaffUser,
    session: SessionDep,
    runner: AgentRunnerDep,
    settings: SettingsDep,
) -> StaffActionResult:
    """Refund, replace, or cancel, then resolve the ticket and tell the customer.

    An override of the business rules needs a written reason; some actions are never allowed.
    Acting twice on the same ticket is a 409, and the shop never acts twice.
    """
    ticket, message, verdict, result = await staff_service.act(
        session,
        ticket_id,
        user,
        body,
        commerce=runner.commerce,
        audit=runner.audit,
        events=runner.events,
        settings=settings,
    )
    return StaffActionResult(
        ticket=TicketRead.model_validate(ticket),
        message=MessageRead.model_validate(message),
        check=verdict,
        reference=result.reference,
        amount=result.amount,
    )


@router.post(
    "/{ticket_id}/close", responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}}
)
async def close(
    ticket_id: uuid.UUID,
    body: StaffCloseRequest,
    user: StaffUser,
    session: SessionDep,
    runner: AgentRunnerDep,
) -> TicketRead:
    """Close without an action: resolved (nothing more to do) or declined."""
    ticket = await staff_service.close(
        session,
        ticket_id,
        user,
        declined=body.outcome == "declined",
        note=body.note,
        audit=runner.audit,
        events=runner.events,
    )
    return TicketRead.model_validate(ticket)
