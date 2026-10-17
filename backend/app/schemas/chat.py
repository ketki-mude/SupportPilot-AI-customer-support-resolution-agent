"""Customer chat requests and responses."""

from pydantic import BaseModel, Field

from app.schemas.ticket import MessageRead, TicketRead


class ChatMessageCreate(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class ChatResponse(BaseModel):
    """The ticket and the messages this request added. Agent replies also arrive on the stream."""

    ticket: TicketRead
    messages: list[MessageRead]
