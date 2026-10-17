"""The shipped policy documents: format, numbering, and consistency with config."""

import re
from pathlib import Path

import pytest

from app.core.config import Settings
from app.rag.chunking import chunk_policy, document_title
from app.rag.ingest import POLICY_DIR

EXPECTED = {
    "billing_policy.md",
    "cancellation_policy.md",
    "refund_policy.md",
    "shipping_policy.md",
}
DOCUMENTS = sorted(POLICY_DIR.glob("*.md"))
_SECTION = re.compile(r"^(#{2,3})\s+(\d+)(?:\.(\d+))?", re.MULTILINE)


def test_all_four_policies_exist() -> None:
    assert {path.name for path in DOCUMENTS} == EXPECTED


@pytest.mark.parametrize("path", DOCUMENTS, ids=lambda p: p.name)
def test_document_parses_and_title_matches_file_name(path: Path) -> None:
    sections = chunk_policy(path.name, path.read_text())
    assert len(sections) >= 4
    assert {s.title for s in sections} == {document_title(path.name)}


@pytest.mark.parametrize("path", DOCUMENTS, ids=lambda p: p.name)
def test_section_numbers_have_no_gaps(path: Path) -> None:
    """Citations must point at real, predictable section numbers."""
    expected_top, expected_sub = 0, 0
    for level, top, sub in _SECTION.findall(path.read_text()):
        if level == "##":
            expected_top, expected_sub = expected_top + 1, 0
            assert int(top) == expected_top, f"{path.name}: section {top}"
        else:
            expected_sub += 1
            assert (int(top), int(sub)) == (expected_top, expected_sub), f"{path.name}: {top}.{sub}"


def test_refund_policy_has_the_damaged_items_section() -> None:
    labels = {
        s.label
        for s in chunk_policy("refund_policy.md", (POLICY_DIR / "refund_policy.md").read_text())
    }
    assert "3.2 Damaged or defective items" in labels


@pytest.mark.parametrize("name", ["refund_policy.md", "cancellation_policy.md"])
def test_documented_window_matches_the_configured_default(name: str) -> None:
    window = Settings.model_fields["refund_window_days"].default
    assert f"within {window} days of" in (POLICY_DIR / name).read_text()


@pytest.mark.parametrize("path", DOCUMENTS, ids=lambda p: p.name)
def test_internal_approval_limits_are_not_published(path: Path) -> None:
    """Dollar thresholds live in config only, never in text the agent retrieves."""
    assert "$" not in path.read_text()
