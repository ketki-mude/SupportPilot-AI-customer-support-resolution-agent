"""Token usage and cost for one agent run."""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.core.config import Settings

_PER_MILLION = Decimal(1_000_000)


@dataclass
class UsageTracker:
    """Accumulates token counts across every model call in a run."""

    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0

    def add(self, usage: Mapping[str, Any] | None) -> None:
        """Add one call's usage metadata; output already includes thinking tokens."""
        self.calls += 1
        if usage:
            self.input_tokens += int(usage.get("input_tokens", 0))
            self.output_tokens += int(usage.get("output_tokens", 0))

    def cost(self, settings: Settings) -> Decimal:
        """Cost in USD at the configured per-million-token prices."""
        total = (
            self.input_tokens * settings.llm_input_usd_per_million
            + self.output_tokens * settings.llm_output_usd_per_million
        ) / _PER_MILLION
        return total.quantize(Decimal("0.000001"))
