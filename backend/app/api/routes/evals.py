"""Evaluations (admin only): run the dataset through the agent and read the saved reports."""

from decimal import Decimal
from http import HTTPStatus
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import AdminUser, SettingsDep
from app.core.errors import ServiceUnavailableError
from app.evals.harness import estimate_cost
from app.evals.jobs import EvalJob, EvalJobs
from app.evals.report import RunReport, RunSummary
from app.schemas.errors import ErrorResponse

router = APIRouter(
    prefix="/evals",
    tags=["evals"],
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}},
)


class EvalRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    llm: Literal["gemini", "mock"] = Field(
        default="gemini", description="gemini: the configured model. mock: offline, no cost."
    )
    limit: int | None = Field(default=None, ge=1, description="Run only the first N cases")


class EvalOverview(BaseModel):
    current: EvalJob | None
    runs: list[RunSummary]
    dataset_cases: int
    model: str
    full_run_estimate_usd: Decimal = Field(description="Estimated live cost of every case")


def get_eval_jobs(request: Request) -> EvalJobs:
    jobs: EvalJobs | None = getattr(request.app.state, "eval_jobs", None)
    if jobs is None:
        raise ServiceUnavailableError("Evaluations are not available")
    return jobs


EvalJobsDep = Annotated[EvalJobs, Depends(get_eval_jobs)]


@router.post(
    "/run",
    status_code=HTTPStatus.ACCEPTED,
    responses={400: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def start_run(_: AdminUser, jobs: EvalJobsDep, body: EvalRunRequest) -> EvalJob:
    """Start a run in the background. Poll GET /api/evals/runs for progress."""
    return jobs.start(body.llm, body.limit)


@router.get("/runs")
async def list_runs(_: AdminUser, jobs: EvalJobsDep, settings: SettingsDep) -> EvalOverview:
    cases = len(jobs.dataset().cases)
    return EvalOverview(
        current=jobs.current,
        runs=jobs.summaries(),
        dataset_cases=cases,
        model=settings.llm_model,
        full_run_estimate_usd=estimate_cost(cases, settings),
    )


@router.get("/runs/{report_id}", responses={404: {"model": ErrorResponse}})
async def get_run(
    _: AdminUser,
    jobs: EvalJobsDep,
    report_id: Annotated[str, Path(pattern=r"^\d{8}T\d{6}Z-(live|mock)$")],
) -> RunReport:
    """One saved report with every case's result."""
    return jobs.report(report_id)
