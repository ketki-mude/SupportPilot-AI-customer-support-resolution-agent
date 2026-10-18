"""The admin evaluation API: start a run, follow it, and read saved reports."""

import shutil
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from langchain_core.embeddings import Embeddings

from app.api.routes.evals import get_eval_jobs
from app.core.config import Settings
from app.evals.harness import evals_dir
from app.evals.jobs import EvalJobs
from tests.agent_support import Api


@pytest.fixture
def jobs(
    api: Api,
    eval_settings: Settings,
    eval_shop: httpx.AsyncBaseTransport,
    embeddings: Embeddings,
    tmp_path: Path,
) -> Iterator[EvalJobs]:
    """Jobs on the in-process shop, saving reports under a temporary evals folder."""
    (tmp_path / "dataset").mkdir()
    shutil.copy(evals_dir(eval_settings) / "dataset" / "cases.jsonl", tmp_path / "dataset")
    settings = eval_settings.model_copy(update={"eval_dir": str(tmp_path)})
    jobs = EvalJobs(settings, lambda: embeddings, transport=eval_shop)
    api.app.dependency_overrides[get_eval_jobs] = lambda: jobs
    yield jobs
    api.app.dependency_overrides.pop(get_eval_jobs)


async def test_admin_runs_an_offline_evaluation(api: Api, jobs: EvalJobs) -> None:
    started = await api.post("/evals/run", "admin", {"llm": "mock", "limit": 2})
    assert started.status_code == 202
    assert started.json()["status"] == "running"
    assert started.json()["cases_planned"] == 2

    again = await api.post("/evals/run", "admin", {"llm": "mock", "limit": 2})
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "evaluation_running"

    await jobs.wait()
    overview = (await api.get("/evals/runs", "admin")).json()
    assert overview["current"]["status"] == "finished"
    assert overview["current"]["cases_done"] == 2
    assert overview["dataset_cases"] == 80
    assert float(overview["full_run_estimate_usd"]) > 0
    [run] = overview["runs"]
    assert run["id"] == overview["current"]["report_id"]
    assert run["llm_mode"] == "mock" and run["complete"]
    assert run["metrics"]["correct_resolution_rate"] == 1.0
    assert "results" not in run

    report = await api.get(f"/evals/runs/{run['id']}", "admin")
    assert report.status_code == 200
    assert [r["id"] for r in report.json()["results"]] == ["refund-auto-01", "refund-approval-01"]


async def test_live_runs_need_a_model_key(api: Api, jobs: EvalJobs) -> None:
    response = await api.post("/evals/run", "admin", {"llm": "gemini"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "llm_not_configured"
    assert jobs.current is None


@pytest.mark.parametrize("who", ["customer", "agent", "supervisor"])
async def test_evaluations_are_admin_only(api: Api, jobs: EvalJobs, who: str) -> None:
    assert (await api.post("/evals/run", who, {"llm": "mock"})).status_code == 403
    assert (await api.get("/evals/runs", who)).status_code == 403


async def test_bad_requests_are_rejected(api: Api, jobs: EvalJobs) -> None:
    assert (await api.post("/evals/run", "admin", {"llm": "mock", "limit": 0})).status_code == 422
    assert (await api.post("/evals/run", "admin", {"llm": "gpt"})).status_code == 422
    assert (await api.get("/evals/runs/..%2Fsecrets", "admin")).status_code in {404, 422}
    missing = await api.get("/evals/runs/20260101T000000Z-live", "admin")
    assert missing.status_code == 404
