"""Evaluation runs started from the API: one at a time, in the background of this process.

The job lives in memory, so a restart forgets a run in progress (its cases are already in the
evaluation database, and nothing is saved until the report is). Finished runs are read back
from the saved reports.
"""

import asyncio
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import httpx
import structlog
from langchain_core.embeddings import Embeddings
from pydantic import BaseModel

from app.agents.llm import LLMConfigError, create_chat_model
from app.core.config import Settings
from app.core.errors import AppError, ConflictError, NotFoundError
from app.evals.dataset import Dataset, load_dataset
from app.evals.harness import LlmMode, evals_dir, run_evaluation
from app.evals.metrics import CaseResult
from app.evals.report import RunReport, RunSummary, list_summaries, load_report

logger = structlog.get_logger(__name__)


class EvalAlreadyRunningError(ConflictError):
    code = "evaluation_running"


class LiveModelUnavailableError(AppError):
    code = "llm_not_configured"


class EvalJob(BaseModel):
    id: str
    status: Literal["running", "finished", "failed"]
    llm: Literal["gemini", "mock"]
    limit: int | None
    cases_planned: int
    cases_done: int = 0
    started_at: datetime
    finished_at: datetime | None = None
    report_id: str | None = None
    error: str | None = None


class EvalJobs:
    def __init__(
        self,
        settings: Settings,
        embeddings: Callable[[], Embeddings],
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._embeddings = embeddings
        self._transport = transport
        self._task: asyncio.Task[None] | None = None
        self.current: EvalJob | None = None

    @property
    def reports_dir(self) -> Path:
        return evals_dir(self._settings) / "reports"

    def dataset(self) -> Dataset:
        return load_dataset(evals_dir(self._settings) / "dataset" / "cases.jsonl")

    def start(self, llm: Literal["gemini", "mock"], limit: int | None) -> EvalJob:
        if self.current is not None and self.current.status == "running":
            raise EvalAlreadyRunningError("An evaluation is already running")
        mode: LlmMode = "mock" if llm == "mock" else "live"
        if mode == "live":
            try:
                create_chat_model(self._settings)
            except LLMConfigError as exc:
                raise LiveModelUnavailableError(str(exc)) from exc
        dataset = self.dataset()
        planned = min(limit or len(dataset.cases), len(dataset.cases))
        job = EvalJob(
            id=uuid.uuid4().hex,
            status="running",
            llm=llm,
            limit=limit,
            cases_planned=planned,
            started_at=datetime.now(UTC),
        )
        self.current = job
        self._task = asyncio.create_task(self._run(job, mode, dataset))
        return job

    async def _run(self, job: EvalJob, mode: LlmMode, dataset: Dataset) -> None:
        async def progress(index: int, total: int, result: CaseResult) -> None:
            job.cases_done = index

        try:
            report = await run_evaluation(
                self._settings,
                self._embeddings(),
                dataset,
                mode=mode,
                limit=job.limit,
                transport=self._transport,
                on_progress=progress,
            )
            report.save(self.reports_dir)
            job.report_id = report.id
            job.status = "finished"
        except Exception as exc:
            logger.exception("evaluation_failed", job_id=job.id)
            job.status = "failed"
            job.error = f"{type(exc).__name__}: {exc}"
        finally:
            job.finished_at = datetime.now(UTC)

    async def wait(self) -> None:
        if self._task is not None:
            await asyncio.gather(self._task, return_exceptions=True)

    def summaries(self) -> list[RunSummary]:
        return list_summaries(self.reports_dir)

    def report(self, report_id: str) -> RunReport:
        path = self.reports_dir / f"{report_id}.json"
        if not path.is_file():
            raise NotFoundError("Evaluation report not found")
        return load_report(path)
