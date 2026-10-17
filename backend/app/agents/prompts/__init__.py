"""System prompts (markdown next to this module) and the data blocks sent alongside them.

Customer text only ever appears inside a <conversation> block in a user message, never in the
system prompt, and angle brackets are escaped so it cannot close the block early.
"""

from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from app.agents.state import AgentState, ChatTurn
from app.schemas.agent import PolicySnippet
from app.schemas.commerce import Order

_DIR = Path(__file__).parent


@lru_cache
def load_prompt(name: str) -> str:
    """The text of prompts/<name>.md."""
    return (_DIR / f"{name}.md").read_text().strip()


def intake_messages(turns: list[ChatTurn], orders: list[Order]) -> list[BaseMessage]:
    listing = "\n".join(_order_line(order) for order in orders) or "(the customer has no orders)"
    return [
        SystemMessage(load_prompt("intake")),
        HumanMessage(f"Customer's orders:\n{listing}\n\n{conversation_block(turns)}"),
    ]


def resolution_messages(state: AgentState) -> list[BaseMessage]:
    intent = state.intent.value if state.intent else "unknown"
    order = _order_facts(state.order) if state.order else "No order is linked to this request."
    return [
        SystemMessage(load_prompt("resolution")),
        HumanMessage(
            f"Intent: {intent}\n\nOrder:\n{order}\n\n"
            f"Policy excerpts:\n{_excerpts(state.policy_snippets)}\n\n"
            f"{conversation_block(state.messages)}"
        ),
    ]


def conversation_block(turns: Iterable[ChatTurn]) -> str:
    lines = "\n".join(f"[{turn.sender.value}] {_escape(turn.content)}" for turn in turns)
    return f"<conversation>\n{lines}\n</conversation>"


def _escape(text: str) -> str:
    return text.replace("<", "&lt;").replace(">", "&gt;")


def _order_line(order: Order) -> str:
    items = ", ".join(item.name for item in order.items)
    when = f"delivered {order.delivered_at:%Y-%m-%d}" if order.delivered_at else "not delivered"
    return (
        f"- {order.order_id} | {order.status.value} | placed {order.placed_at:%Y-%m-%d} | "
        f"{when} | {items}"
    )


def _order_facts(order: Order) -> str:
    items = "\n".join(
        f"  - SKU {i.sku}: {i.name}, quantity {i.quantity}, unit price {i.unit_price}"
        for i in order.items
    )
    dates = [
        f"placed {order.placed_at:%Y-%m-%d}",
        f"shipped {order.shipped_at:%Y-%m-%d}" if order.shipped_at else None,
        f"delivered {order.delivered_at:%Y-%m-%d}" if order.delivered_at else None,
        f"estimated delivery {order.estimated_delivery_at:%Y-%m-%d}"
        if order.estimated_delivery_at
        else None,
        f"cancelled {order.cancelled_at:%Y-%m-%d}" if order.cancelled_at else None,
    ]
    remedy = order.remedy.value if order.remedy else "none"
    return (
        f"Order {order.order_id}, status {order.status.value}, "
        f"payment {order.payment_status.value}, "
        f"total {order.total} {order.currency}, refunded so far {order.refunded_amount}, "
        f"previous remedy {remedy}.\n"
        f"Dates: {', '.join(d for d in dates if d)}.\n"
        f"Tracking: {order.tracking_number or 'none'}.\nItems:\n{items}"
    )


def _excerpts(snippets: list[PolicySnippet]) -> str:
    if not snippets:
        return "(no relevant policy found)"
    return "\n\n".join(
        f"[source: {s.source} | section: {s.section}]\n{s.content}" for s in snippets
    )
