"""Health check response."""

from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    database: Literal["ok", "unavailable"]
    version: str
    # Informational: an open circuit means the agent is escalating instead of calling the shop.
    commerce_circuit: Literal["closed", "open", "half_open"] | None = None
