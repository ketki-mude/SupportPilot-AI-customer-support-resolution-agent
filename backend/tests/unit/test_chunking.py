"""Policy markdown chunking."""

import pytest

from app.rag.chunking import MAX_CHUNK_CHARS, chunk_policy, citation, document_title

SAMPLE = """# Refund policy

Intro text that belongs to no section.

## 1. Eligibility

### 1.1 Delivered orders only
Refunds start after delivery.

### 1.2 Refund window
Within 30 days.

## 2. Timing
Refunds post in 5 to 10 business days.
"""


def test_sections_carry_number_heading_and_body() -> None:
    sections = chunk_policy("refund_policy.md", SAMPLE)
    assert [(s.number, s.heading, s.content) for s in sections] == [
        ("1.1", "Delivered orders only", "Refunds start after delivery."),
        ("1.2", "Refund window", "Within 30 days."),
        ("2", "Timing", "Refunds post in 5 to 10 business days."),
    ]
    assert {s.title for s in sections} == {"Refund policy"}
    assert {s.source for s in sections} == {"refund_policy.md"}


def test_headings_without_body_text_are_not_chunks() -> None:
    numbers = [s.number for s in chunk_policy("refund_policy.md", SAMPLE)]
    assert "1" not in numbers


def test_label_and_embedding_text() -> None:
    section = chunk_policy("refund_policy.md", SAMPLE)[1]
    assert section.label == "1.2 Refund window"
    assert section.embedding_text.startswith("Refund policy, section 1.2: Refund window.")
    assert "Within 30 days." in section.embedding_text


def test_citation_and_title_helpers() -> None:
    assert document_title("cancellation_policy.md") == "Cancellation policy"
    assert (
        citation("refund_policy.md", "3.2 Damaged or defective items")
        == "Refund policy, section 3.2: Damaged or defective items"
    )


def test_unnumbered_heading_is_rejected() -> None:
    with pytest.raises(ValueError, match="not numbered"):
        chunk_policy("x.md", "# Title\n\n## Eligibility\ntext\n")


def test_missing_title_is_rejected() -> None:
    with pytest.raises(ValueError, match="missing"):
        chunk_policy("x.md", "## 1. Eligibility\ntext\n")


def test_long_sections_are_split_on_paragraphs() -> None:
    paragraph = "word " * 100
    body = "\n\n".join(paragraph.strip() for _ in range(8))
    sections = chunk_policy("x.md", f"# Title\n\n## 1. Long\n{body}\n")
    assert len(sections) > 1
    assert {s.number for s in sections} == {"1"}
    assert all(len(s.content) <= MAX_CHUNK_CHARS for s in sections)
    assert " ".join(s.content for s in sections).split() == body.split()
