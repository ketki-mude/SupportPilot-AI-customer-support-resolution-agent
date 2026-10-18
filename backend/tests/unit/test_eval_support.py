"""The evaluation dataset, the offline keyword stand-in, reports, and the command line."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from app.agents.prompts import intake_messages, resolution_messages
from app.agents.state import AgentState, ChatTurn
from app.core.config import get_settings
from app.db.models import MessageSender, TicketCategory
from app.evals.cli import gate_failures, parse_args, render_saved, update_readme
from app.evals.dataset import EvalCase, load_dataset
from app.evals.harness import estimate_cost, eval_database_url, evals_dir
from app.evals.metrics import aggregate, score
from app.evals.mock_llm import KeywordLLM
from app.evals.report import (
    README_END,
    README_START,
    RunReport,
    list_summaries,
    percent,
    render_markdown,
    replace_readme_results,
)
from app.observability.cost import UsageTracker
from app.schemas.agent import IntakeOutput, PolicySnippet, ProposedAction, ResolutionOutput
from app.schemas.commerce import Order, OrderItem, OrderStatus, PaymentStatus
from tests.unit.test_eval_scoring import make_case, make_obs

NOW = datetime.now(UTC)
DATASET = evals_dir(get_settings()) / "dataset" / "cases.jsonl"


def _order(order_id: str, name: str) -> Order:
    return Order(
        order_id=order_id,
        customer_id="cus_100",
        status=OrderStatus.DELIVERED,
        payment_status=PaymentStatus.CAPTURED,
        items=[OrderItem(sku="SKU-1", name=name, quantity=1, unit_price=Decimal("10.00"))],
        total=Decimal("10.00"),
        currency="USD",
        placed_at=NOW - timedelta(days=9),
        shipped_at=NOW - timedelta(days=7),
        delivered_at=NOW - timedelta(days=4),
        estimated_delivery_at=None,
        cancelled_at=None,
        tracking_number=None,
        payment_method_last4="4242",
        refunded_amount=Decimal("0.00"),
        remedy=None,
    )


# --- Dataset


def test_the_dataset_is_valid() -> None:
    dataset = load_dataset(DATASET)
    assert len(dataset.cases) == 80
    smoke = dataset.cases[:10]
    assert len({case.category for case in smoke}) == 10, "the smoke set covers ten paths"
    assert any(case.supervisor == "reject" for case in dataset.cases)
    assert any(case.faults for case in dataset.cases)


def test_dataset_rejects_duplicates_and_bad_cases(tmp_path: Path) -> None:
    line = make_case().model_dump_json()
    path = tmp_path / "cases.jsonl"
    path.write_text(f"{line}\n{line}\n")
    with pytest.raises(ValueError, match="Duplicate case ids: case-1"):
        load_dataset(path)
    with pytest.raises(ValueError, match="not in the fixture"):
        make_case(customer_id="cus_999")
    with pytest.raises(ValueError, match="Extra inputs"):
        make_case(surprise=True)


# --- Keyword stand-in, fed the real prompts


async def test_keyword_llm_reads_the_real_intake_prompt() -> None:
    turns = [ChatTurn(sender=MessageSender.CUSTOMER, content="My headphones broke, refund please")]
    orders = [_order("ORD-1", "Aurora wireless headphones"), _order("ORD-2", "Cotton t-shirt")]
    answer = await KeywordLLM().generate(
        IntakeOutput, intake_messages(turns, orders), UsageTracker()
    )
    assert answer.intent is TicketCategory.REFUND
    assert answer.matching_order_ids == ["ORD-1"]


async def test_keyword_llm_answers_info_from_the_excerpt() -> None:
    snippet = PolicySnippet(
        source="shipping_policy.md",
        section="2.1 Tracking numbers",
        content="Use tracking.",
        score=1,
    )
    state = AgentState(
        ticket_id="t",
        customer_id="cus_100",
        intent=TicketCategory.MISSING_ORDER,
        messages=[ChatTurn(sender=MessageSender.CUSTOMER, content="Where is my order?")],
        policy_snippets=[snippet],
    )
    answer = await KeywordLLM().generate(
        ResolutionOutput, resolution_messages(state), UsageTracker()
    )
    assert answer.action is ProposedAction.INFO
    assert answer.customer_reply == "Use tracking."
    assert [c.section for c in answer.citations] == ["2.1 Tracking numbers"]


async def test_keyword_llm_refuses_unknown_schemas() -> None:
    with pytest.raises(TypeError):
        await KeywordLLM().generate(AgentState, [], UsageTracker())  # type: ignore[arg-type]


# --- Reports


@pytest.mark.parametrize(
    ("part", "whole", "shown"),
    [(2, 3, "66.6%"), (1, 8, "12.5%"), (79, 80, "98.7%"), (80, 80, "100.0%"), (0, 0, "n/a")],
)
def test_percentages_are_truncated(part: int, whole: int, shown: str) -> None:
    assert percent(part, whole) == shown


def _report(**overrides: Any) -> RunReport:
    results = [
        score(make_case(id="ok"), make_obs()),
        score(make_case(id="bad", expected_action="deny", must_not=["refund"]), make_obs()),
    ]
    values: dict[str, Any] = {
        "started_at": datetime(2026, 10, 17, 9, 30, tzinfo=UTC),
        "duration_seconds": 12.5,
        "mode": "live",
        "settings": get_settings().model_copy(update={"eval_git_commit": "abc1234"}),
        "dataset": load_dataset(DATASET),
        "limit": None,
        "cases_planned": 2,
        "estimated_cost_usd": Decimal("0.0055"),
        "stopped_reason": None,
        "metrics": aggregate(results),
        "results": results,
    }
    return RunReport.create(**(values | overrides))


def test_report_records_provenance_and_failures(tmp_path: Path) -> None:
    report = _report()
    assert report.id == "20261017T093000Z-live"
    assert report.git_commit == "abc1234"
    assert report.temperature == get_settings().llm_temperature
    assert report.complete
    markdown = render_markdown(report)
    assert "| Correct resolution | 50.0% | 1 of 2 cases |" in markdown
    assert "## Failed cases (1)" in markdown
    assert "`bad`" in markdown and "forbidden effect: refund" in markdown
    assert "**Unauthorized actions in:** `bad`" in markdown
    assert "abc1234" in markdown and get_settings().llm_model in markdown

    report.save(tmp_path)
    (tmp_path / "broken.json").write_text("{not json")
    [summary] = list_summaries(tmp_path)
    assert summary.id == report.id
    assert "results" not in summary.model_dump()


def test_a_stopped_run_is_marked_incomplete() -> None:
    report = _report(stopped_reason="Model quota exhausted", cases_planned=5)
    assert not report.complete
    assert "**Stopped early:** Model quota exhausted" in render_markdown(report)
    assert gate_failures(report, None)[0] == "the run is incomplete: Model quota exhausted"


def test_gate_checks_unauthorized_actions_and_the_pass_rate() -> None:
    failures = gate_failures(_report(), 1.0)
    assert failures == [
        "1 case(s) took an unauthorized action",
        "correct resolution 50.0% is below 100%",
    ]


def test_mock_reports_name_no_model() -> None:
    report = _report(mode="mock", estimated_cost_usd=Decimal(0))
    assert report.model == "keyword stand-in (no model)"
    assert report.provider == "none"
    assert report.temperature is None
    assert "no sampling (keyword rules)" in render_markdown(report)


def test_reports_say_when_the_model_fixes_its_own_temperature() -> None:
    settings = get_settings().model_copy(update={"llm_temperature": None})
    report = _report(settings=settings)
    assert report.temperature is None
    assert "its fixed default temperature (the model accepts no other)" in render_markdown(report)


def test_documents_can_be_rendered_from_a_saved_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    saved = _report().save(tmp_path)
    readme = tmp_path / "README.md"
    readme.write_text(f"{README_START}\nold\n{README_END}\n")
    monkeypatch.setattr("app.evals.cli.default_readme_path", lambda: readme)
    docs = tmp_path / "docs" / "eval-report.md"
    assert render_saved(saved, docs) == 0
    assert "## Failed cases (1)" in docs.read_text()
    assert "old" not in readme.read_text()


# --- Command line and helpers


def test_cli_arguments() -> None:
    args = parse_args(["--llm", "mock", "--limit", "10", "--gate", "--min-correct", "1"])
    assert (args.llm, args.limit, args.gate, args.min_correct) == ("mock", 10, True, 1.0)
    assert parse_args([]).llm == "gemini"
    with pytest.raises(SystemExit):
        parse_args(["--limit", "0"])
    with pytest.raises(SystemExit):
        parse_args(["--docs", "x.md", "--no-docs"])


def test_cost_estimate_uses_configured_prices() -> None:
    settings = get_settings().model_copy(
        update={
            "llm_input_usd_per_million": Decimal("0.30"),
            "llm_output_usd_per_million": Decimal("2.50"),
        }
    )
    # 1,700 input and 900 output tokens per case: $0.00051 + $0.00225.
    assert estimate_cost(10, settings) == Decimal("0.0276")


def test_eval_database_is_separate_from_the_app_database() -> None:
    settings = get_settings()
    url = eval_database_url(settings)
    assert url.rsplit("/", 1)[1].endswith("_eval")
    assert url != settings.database_url
    override = settings.model_copy(update={"eval_database_url": "postgresql+asyncpg://x/y_eval"})
    assert eval_database_url(override) == "postgresql+asyncpg://x/y_eval"


def test_dataset_file_is_one_object_per_line() -> None:
    for line in DATASET.read_text().splitlines():
        EvalCase.model_validate(json.loads(line))


def test_readme_results_come_only_from_complete_full_live_runs(tmp_path: Path) -> None:
    readme = tmp_path / "README.md"
    original = f"# Title\n\n{README_START}\nNot run yet.\n{README_END}\n\nMore.\n"
    readme.write_text(original)

    assert not update_readme(_report(limit=10), readme)
    assert not update_readme(_report(stopped_reason="quota"), readme)
    assert not update_readme(_report(mode="mock"), readme)
    assert readme.read_text() == original

    assert update_readme(_report(), readme)
    text = readme.read_text()
    assert text.startswith("# Title") and text.endswith("More.\n")
    assert "Not run yet." not in text
    assert "| Correct resolution | 50.0% (1 of 2) |" in text
    assert "| Unauthorized actions | 1 of 2 |" in text
    assert "evals/reports/20261017T093000Z-live.json" in text
    assert "commit `abc1234`" in text


def test_readme_without_markers_is_refused() -> None:
    with pytest.raises(ValueError, match="markers"):
        replace_readme_results("# No markers", "block")
