"""Split policy markdown into numbered sections, the unit of retrieval and citation.

Expected format: one "# Title" line, then "## 1. Heading" and "### 1.1 Heading" sections.
Every section heading must be numbered so answers can cite it.
"""

import re
from dataclasses import dataclass
from pathlib import Path

MAX_CHUNK_CHARS = 1200

_HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*$")
_NUMBERED = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+(.+)$")


@dataclass(frozen=True, slots=True)
class PolicySection:
    source: str
    title: str
    number: str
    heading: str
    content: str

    @property
    def label(self) -> str:
        """Stored as policy_chunks.section, e.g. "3.2 Damaged or defective items"."""
        return f"{self.number} {self.heading}"

    @property
    def embedding_text(self) -> str:
        """The heading is embedded with the body; it carries much of the meaning."""
        return f"{self.title}, section {self.number}: {self.heading}.\n{self.content}"


def document_title(source: str) -> str:
    """'refund_policy.md' -> 'Refund policy'. Policy files are titled to match."""
    return Path(source).stem.replace("_", " ").capitalize()


def citation(source: str, label: str) -> str:
    """'Refund policy, section 3.2: Damaged or defective items'."""
    number, _, heading = label.partition(" ")
    return f"{document_title(source)}, section {number}: {heading}"


def chunk_policy(source: str, markdown: str) -> list[PolicySection]:
    """Return one section per numbered heading that has body text, split if very long."""
    title: str | None = None
    sections: list[PolicySection] = []
    number = heading = ""
    body: list[str] = []

    def flush() -> None:
        text = "\n".join(body).strip()
        if number and text and title:
            sections.extend(
                PolicySection(source, title, number, heading, part) for part in _split(text)
            )
        body.clear()

    for line in markdown.splitlines():
        match = _HEADING.match(line)
        if match is None:
            body.append(line)
            continue
        flush()
        level, text = len(match.group(1)), match.group(2)
        if level == 1:
            title = text
            number = ""
            continue
        numbered = _NUMBERED.match(text)
        if numbered is None:
            raise ValueError(f"{source}: section heading is not numbered: {text!r}")
        number, heading = numbered.group(1), numbered.group(2)
    flush()

    if title is None:
        raise ValueError(f"{source}: missing '# Title' line")
    return sections


def _split(text: str) -> list[str]:
    """Pack paragraphs into parts no longer than MAX_CHUNK_CHARS (a single paragraph may be)."""
    parts: list[str] = []
    current = ""
    for paragraph in (p.strip() for p in text.split("\n\n")):
        if not paragraph:
            continue
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if current and len(candidate) > MAX_CHUNK_CHARS:
            parts.append(current)
            current = paragraph
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts
