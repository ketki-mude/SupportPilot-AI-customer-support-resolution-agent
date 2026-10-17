"""Optional LangSmith tracing, with personal data scrubbed before anything leaves the process.

LangChain traces automatically whenever LANGSMITH_TRACING is set in the environment, but that
path sends raw tool outputs (customer emails and phones included). So the automatic path is
switched off, and a tracer with an anonymizer is attached to each agent run instead.
"""

import os
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.tracers.langchain import LangChainTracer
from langsmith import Client
from langsmith.anonymizer import create_anonymizer

from app.agents.guardrails import redact_pii
from app.core.config import Settings

_AMBIENT_TRACING_VARS = ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING")


def disable_ambient_tracing() -> None:
    for name in _AMBIENT_TRACING_VARS:
        os.environ[name] = "false"


def _redact(text: str, _path: list[str | int]) -> str:
    return redact_pii(text)


scrub = create_anonymizer(_redact)


def build_tracer(settings: Settings) -> BaseCallbackHandler | None:
    """A scrubbing LangSmith tracer when LANGSMITH_TRACING is on and a key is set."""
    if not settings.langsmith_tracing or settings.langsmith_api_key is None:
        return None
    client = Client(api_key=settings.langsmith_api_key.get_secret_value(), anonymizer=scrub)
    return LangChainTracer(project_name=settings.langsmith_project, client=client)


def scrubbed(value: Any) -> Any:
    """Apply the trace anonymizer to any nested value (used in tests and for spot checks)."""
    return scrub(value)
