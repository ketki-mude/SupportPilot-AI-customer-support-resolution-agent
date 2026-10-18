"""Staff handling of a ticket: reply to the customer, carry out an action, or close it.

People take over where the agent stops, usually on escalated tickets. Actions go through the
same audited shop tools and per-ticket idempotency keys as the agent, so a double click or a
retry never acts twice. Every action is first checked against the business rules:

- allowed: the rules permit it. The person acting is the approver for anything that needed one.
- override: the rules would refuse it or hand it to a specialist (over $500, outside the window,
  not delivered yet or late, a flagged account). It goes ahead only with a written reason, which
  is recorded in the audit log under the staff member's name.
- blocked: never allowed, whoever asks: another customer's order, an order that already had its
  refund or replacement, an item that is not on the order, or an order the shop cannot replace
  or cancel in its current state.
"""

import uuid
from decimal import Decimal

from langchain_core.tools import StructuredTool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import replies
from app.agents.runtime import EventBus, EventType
from app.agents.state import ActionResult
from app.agents.tools import build_tools
from app.agents.tools.base import ToolContext
from app.agents.tools.commerce import (
    CommerceClient,
    CommerceNotFoundError,
    CommerceRejectedError,
    CommerceUnavailableError,
)
from app.core.config import Settings
from app.core.errors import AppError, ConflictError, ServiceUnavailableError
from app.db.models import (
    AgentRun,
    AgentRunStatus,
    Message,
    MessageSender,
    Ticket,
    TicketCategory,
    TicketStatus,
    User,
)
from app.schemas.agent import Decision, Proposal, ProposedAction, Rule
from app.schemas.commerce import Customer, Order, OrderStatus, PaymentStatus
from app.schemas.staff import (
    MIN_OVERRIDE_REASON,
    ActionCheck,
    StaffActionCheckRequest,
    StaffActionKind,
    StaffActionRequest,
    Verdict,
)
from app.services import ticket_service
from app.services.audit_service import AuditService, AuditStatus
from app.services.policy_engine import PolicyContext, evaluate, remedy_value

# Staff act on tickets the agent is not handling: escalated ones, or open ones it left waiting.
# A ticket waiting for approval has its own approval bar; a closed ticket stays closed.
ACTIONABLE = frozenset({TicketStatus.OPEN, TicketStatus.ESCALATED})
_INTENT: dict[StaffActionKind, TicketCategory] = {
    "refund": TicketCategory.REFUND,
    "replacement": TicketCategory.REPLACEMENT,
    "cancel": TicketCategory.CANCELLATION,
}
_CANCEL_BLOCKED = {
    OrderStatus.SHIPPED: (Rule.CANCEL_AFTER_SHIPMENT, "Shipped orders can't be cancelled."),
    OrderStatus.IN_TRANSIT: (Rule.CANCEL_AFTER_SHIPMENT, "Orders in transit can't be cancelled."),
    OrderStatus.DELIVERED: (Rule.CANCEL_AFTER_DELIVERY, "Delivered orders can't be cancelled."),
    OrderStatus.CANCELLED: (Rule.ALREADY_CANCELLED, "The order is already cancelled."),
}


class TicketNotActionableError(ConflictError):
    code = "ticket_not_actionable"


class ActionNotAllowedError(ConflictError):
    code = "action_not_allowed"


class OverrideReasonRequiredError(AppError):
    status_code = 422
    code = "override_reason_required"


def check_action(
    request: StaffActionCheckRequest,
    *,
    ticket_customer_id: str,
    customer: Customer,
    order: Order | None,
    settings: Settings,
) -> ActionCheck:
    """Classify an action as allowed, an override, or blocked. Pure: no I/O."""
    action = request.action

    def verdict(
        kind: Verdict, rule: Rule | str, reason: str, amount: Decimal | None = None
    ) -> ActionCheck:
        return ActionCheck(
            action=action,
            order_id=order.order_id if order else None,
            verdict=kind,
            rule=str(rule),
            reason=reason,
            amount=amount,
            skus=skus,
        )

    skus: list[str] = list(request.skus)
    if order is None:
        return verdict("blocked", Rule.ORDER_REQUIRED, "Choose the order this is about.")
    if order.customer_id != ticket_customer_id:
        return verdict(
            "blocked", Rule.OWNERSHIP_MISMATCH, "This order belongs to a different customer."
        )

    amount: Decimal | None
    if action == "cancel":
        if order.status in _CANCEL_BLOCKED:
            rule, reason = _CANCEL_BLOCKED[order.status]
            return verdict("blocked", rule, reason)
        amount = order.total if order.payment_status is PaymentStatus.CAPTURED else None
    else:
        value = remedy_value(order, skus)
        if value is None:
            return verdict("blocked", Rule.UNKNOWN_ITEM, "An item is not on this order.")
        amount, skus = value
        if order.remedy is not None or order.refunded_amount > 0:
            already = order.remedy.value if order.remedy else "refund"
            return verdict(
                "blocked",
                Rule.REMEDY_ALREADY_APPLIED,
                f"This order already had a {already}; each order gets one.",
                amount,
            )
        if action == "replacement" and order.status is not OrderStatus.DELIVERED:
            return verdict(
                "blocked", Rule.NOT_DELIVERED, "Only delivered orders can be replaced.", amount
            )

    proposed = ProposedAction(action)
    decision = evaluate(
        Proposal(action=proposed, skus=skus, reason="Support team action", confidence=1.0),
        PolicyContext(
            requester_customer_id=ticket_customer_id,
            intent=_INTENT[action],
            customer=customer,
            order=order,
        ),
        settings,
    )
    permitted = decision.decision in (Decision.AUTO_APPROVE, Decision.NEEDS_APPROVAL)
    if permitted and decision.action is proposed:
        return verdict("allowed", decision.rule, decision.reason, decision.amount or amount)
    return verdict("override", decision.rule, decision.reason, amount)


async def check(
    session: AsyncSession,
    ticket_id: uuid.UUID,
    request: StaffActionCheckRequest,
    *,
    commerce: CommerceClient,
    settings: Settings,
) -> ActionCheck:
    ticket = await ticket_service.get_ticket(session, ticket_id)
    customer, order = await _load(commerce, ticket, request.order_id)
    return check_action(
        request,
        ticket_customer_id=ticket.customer_id,
        customer=customer,
        order=order,
        settings=settings,
    )


async def act(
    session: AsyncSession,
    ticket_id: uuid.UUID,
    user: User,
    request: StaffActionRequest,
    *,
    commerce: CommerceClient,
    audit: AuditService,
    events: EventBus,
    settings: Settings,
) -> tuple[Ticket, Message, ActionCheck, ActionResult]:
    """Check, then carry out the action, resolve the ticket, and tell the customer."""
    ticket = await _lock_actionable(session, ticket_id)
    customer, order = await _load(commerce, ticket, request.order_id)
    verdict = check_action(
        request,
        ticket_customer_id=ticket.customer_id,
        customer=customer,
        order=order,
        settings=settings,
    )
    note = (request.note or "").strip()
    if verdict.verdict == "blocked" or order is None:
        raise ActionNotAllowedError(verdict.reason)
    if verdict.verdict == "override" and len(note) < MIN_OVERRIDE_REASON:
        raise OverrideReasonRequiredError(
            f"{verdict.reason} To override, write a reason of at least {MIN_OVERRIDE_REASON} "
            "characters."
        )

    actor = str(user.id)
    tools = build_tools(
        ToolContext(
            ticket_id=ticket.id,
            customer_id=ticket.customer_id,
            session=session,
            commerce=commerce,
            audit=audit,
            actor=actor,
        )
    )
    reason = (note or "Support team decision")[:500]
    try:
        result = await _carry_out(tools, verdict, order.order_id, reason)
    except CommerceRejectedError as exc:
        raise ActionNotAllowedError(f"The shop refused: {exc}") from exc
    except CommerceUnavailableError as exc:
        raise ServiceUnavailableError("The shop is not responding; try again shortly") from exc

    ticket.status = TicketStatus.RESOLVED
    ticket.order_id = order.order_id
    ticket.customer_replied = False
    message = await ticket_service.add_message(
        session, ticket.id, MessageSender.STAFF, replies.action_done(result, order)
    )
    await session.commit()
    await audit.record(
        ticket_id=ticket.id,
        actor=actor,
        action="staff_action",
        status=AuditStatus.SUCCESS,
        input_data={
            "action": verdict.action,
            "order_id": order.order_id,
            "skus": verdict.skus,
            "verdict": verdict.verdict,
            "rule": verdict.rule,
            "note": note or None,
        },
        output_data=result,
    )
    _publish(events, ticket, message)
    return ticket, message, verdict, result


async def reply(
    session: AsyncSession,
    ticket_id: uuid.UUID,
    user: User,
    text: str,
    *,
    audit: AuditService,
    events: EventBus,
) -> Message:
    """A message from the support team, on any ticket. It answers any pending customer reply."""
    ticket = await ticket_service.get_ticket(session, ticket_id)
    message = await ticket_service.add_message(session, ticket.id, MessageSender.STAFF, text)
    ticket.customer_replied = False
    await session.commit()
    await audit.record(
        ticket_id=ticket.id,
        actor=str(user.id),
        action="staff_reply",
        status=AuditStatus.SUCCESS,
        output_data={"message_id": str(message.id)},
    )
    _publish(events, ticket, message, completed=False)
    return message


async def close(
    session: AsyncSession,
    ticket_id: uuid.UUID,
    user: User,
    *,
    declined: bool,
    note: str | None,
    audit: AuditService,
    events: EventBus,
) -> Ticket:
    """Close without an action, as resolved (nothing more to do) or declined."""
    ticket = await _lock_actionable(session, ticket_id)
    ticket.status = TicketStatus.REJECTED if declined else TicketStatus.RESOLVED
    ticket.customer_replied = False
    message = await ticket_service.add_message(
        session,
        ticket.id,
        MessageSender.STAFF,
        replies.STAFF_CLOSED_DECLINED if declined else replies.STAFF_CLOSED_RESOLVED,
    )
    await session.commit()
    await audit.record(
        ticket_id=ticket.id,
        actor=str(user.id),
        action="staff_close",
        status=AuditStatus.SUCCESS,
        input_data={"outcome": "declined" if declined else "resolved", "note": note},
    )
    _publish(events, ticket, message)
    return ticket


async def _lock_actionable(session: AsyncSession, ticket_id: uuid.UUID) -> Ticket:
    """Lock the ticket so two staff members, or two clicks, cannot both act on it.

    FOR NO KEY UPDATE, not FOR UPDATE: audit entries written meanwhile on their own connections
    reference this row, and a plain FOR UPDATE would block those inserts until we commit.
    """
    ticket = await session.scalar(
        select(Ticket).where(Ticket.id == ticket_id).with_for_update(key_share=True)
    )
    if ticket is None:
        raise ticket_service.TicketNotFoundError("Ticket not found")
    if ticket.status is TicketStatus.AWAITING_APPROVAL:
        raise TicketNotActionableError("This ticket is waiting for an approval decision")
    if ticket.status not in ACTIONABLE:
        raise TicketNotActionableError("This ticket is already closed")
    latest = await session.scalar(
        select(AgentRun.status)
        .where(AgentRun.ticket_id == ticket_id)
        .order_by(AgentRun.created_at.desc())
        .limit(1)
    )
    if latest is AgentRunStatus.RUNNING:
        raise TicketNotActionableError("The agent is still working on this ticket")
    return ticket


async def _load(
    commerce: CommerceClient, ticket: Ticket, order_id: str | None
) -> tuple[Customer, Order | None]:
    try:
        customer = await commerce.get_customer(ticket.customer_id)
        wanted = order_id or ticket.order_id
        return customer, (await commerce.get_order(wanted) if wanted else None)
    except CommerceNotFoundError as exc:
        raise ActionNotAllowedError(str(exc)) from exc
    except CommerceUnavailableError as exc:
        raise ServiceUnavailableError("The shop is not responding; try again shortly") from exc


async def _carry_out(
    tools: dict[str, StructuredTool], verdict: ActionCheck, order_id: str, reason: str
) -> ActionResult:
    if verdict.action == "refund":
        refund = await tools["create_refund"].ainvoke(
            {"order_id": order_id, "amount": f"{verdict.amount:.2f}", "reason": reason}
        )
        return ActionResult(
            action=ProposedAction.REFUND, reference=refund.refund_id, amount=refund.amount
        )
    if verdict.action == "replacement":
        replacement = await tools["create_replacement"].ainvoke(
            {"order_id": order_id, "skus": verdict.skus, "reason": reason}
        )
        return ActionResult(
            action=ProposedAction.REPLACEMENT,
            reference=replacement.replacement_id,
            amount=verdict.amount,
        )
    cancellation = await tools["cancel_order"].ainvoke({"order_id": order_id, "reason": reason})
    return ActionResult(
        action=ProposedAction.CANCEL,
        reference=order_id,
        amount=cancellation.refund.amount if cancellation.refund else None,
        payment_action=cancellation.payment_action,
    )


def _publish(events: EventBus, ticket: Ticket, message: Message, *, completed: bool = True) -> None:
    """Push the message, and the new status, to anyone watching the ticket live."""
    key = str(ticket.id)
    events.publish(
        key,
        EventType.MESSAGE,
        {"message_id": str(message.id), "sender": message.sender.value, "content": message.content},
    )
    if completed:
        events.publish(key, EventType.COMPLETED, {"ticket_status": ticket.status.value})
