"""Public representation of a user."""

import uuid

from pydantic import BaseModel, ConfigDict

from app.core.security import Role


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    role: Role
    customer_id: str | None
