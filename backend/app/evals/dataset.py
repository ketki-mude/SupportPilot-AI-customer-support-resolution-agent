"""The evaluation dataset: one JSON object per line in evals/dataset/cases.jsonl."""

import hashlib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.db.models import TicketCategory

# What the system finally does about a case.
Action = Literal["refund", "replacement", "cancel", "info", "escalate", "deny", "clarify"]
# Effects a case may forbid. The last two are always forbidden, for every case.
Effect = Literal[
    "refund", "replacement", "cancel", "money_without_approval", "act_on_other_customer"
]


class EvalCase(BaseModel):
    """One scenario: a customer, their shop data, a message, and what must happen."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9-]+$")
    category: str
    customer_id: str
    customer_message: str = Field(min_length=1, max_length=2000)
    fixture: dict[str, Any] = Field(description="Shop data in the mock seed-file format")
    faults: dict[str, Any] = Field(default_factory=dict, description="Mock shop faults")
    supervisor: Literal["approve", "reject"] = "approve"
    expected_intent: TicketCategory | None = Field(
        description="None when classification is not expected to run, e.g. screened attacks"
    )
    expected_action: Action
    expected_requires_approval: bool
    must_not: list[Effect] = Field(default_factory=list)
    notes: str = ""

    @model_validator(mode="after")
    def _customer_is_in_fixture(self) -> "EvalCase":
        if self.customer_id not in {c["id"] for c in self.fixture.get("customers", [])}:
            raise ValueError(f"{self.id}: customer {self.customer_id} is not in the fixture")
        return self


class Dataset(BaseModel):
    path: str
    sha256: str
    cases: list[EvalCase]


def load_dataset(path: Path) -> Dataset:
    """Read and validate every case; ids must be unique."""
    raw = path.read_bytes()
    cases = [
        EvalCase.model_validate_json(line) for line in raw.decode().splitlines() if line.strip()
    ]
    ids = [case.id for case in cases]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ValueError(f"Duplicate case ids: {', '.join(duplicates)}")
    return Dataset(path=str(path), sha256=hashlib.sha256(raw).hexdigest(), cases=cases)
