"""Command line for evaluations. Run `python evals/run_evals.py --help`."""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

from app.agents.llm import LLMConfigError, create_chat_model
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.evals.dataset import load_dataset
from app.evals.harness import (
    BACKEND_DIR,
    TYPICAL_INPUT_TOKENS,
    TYPICAL_OUTPUT_TOKENS,
    LlmMode,
    estimate_cost,
    evals_dir,
    run_evaluation,
)
from app.evals.metrics import CaseResult
from app.evals.report import (
    RunReport,
    load_report,
    percent,
    render_markdown,
    render_readme_results,
    replace_readme_results,
)
from app.rag.embeddings import get_embeddings

EXIT_GATE_FAILED = 1
EXIT_CONFIG = 2
EXIT_CANCELLED = 3


def default_docs_path() -> Path:
    return BACKEND_DIR.parent / "docs" / "eval-report.md"


def default_readme_path() -> Path:
    return BACKEND_DIR.parent / "README.md"


def update_readme(report: RunReport, path: Path) -> bool:
    """Refresh the README's results block, only from a complete live run of every case."""
    if report.llm_mode != "live" or not report.complete or report.limit is not None:
        return False
    if not path.is_file():
        return False
    path.write_text(replace_readme_results(path.read_text(), render_readme_results(report)))
    return True


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="run_evals.py",
        description="Run the evaluation dataset through the real agent graph and save a report.",
    )
    parser.add_argument(
        "--llm",
        choices=["gemini", "mock"],
        default="gemini",
        help="gemini: the configured model (needs its API key). "
        "mock: an offline keyword stand-in, for CI. Default: gemini.",
    )
    parser.add_argument("--limit", type=int, metavar="N", help="Run only the first N cases")
    parser.add_argument("--dataset", type=Path, help="Default: evals/dataset/cases.jsonl")
    parser.add_argument("--yes", action="store_true", help="Skip the cost confirmation")
    parser.add_argument(
        "--render",
        type=Path,
        metavar="REPORT",
        help="Run nothing: rewrite the Markdown report and the README results block from a "
        "saved report file",
    )
    docs = parser.add_mutually_exclusive_group()
    docs.add_argument(
        "--docs",
        type=Path,
        metavar="PATH",
        help="Also write the Markdown report here. Default for live runs: docs/eval-report.md",
    )
    docs.add_argument("--no-docs", action="store_true", help="Do not write the Markdown report")
    parser.add_argument(
        "--gate",
        action="store_true",
        help="Exit with status 1 unless the run completed with zero unauthorized actions",
    )
    parser.add_argument(
        "--min-correct",
        type=float,
        metavar="RATE",
        help="With --gate, also require this correct-resolution rate (0 to 1)",
    )
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    if args.min_correct is not None and not 0 <= args.min_correct <= 1:
        parser.error("--min-correct must be between 0 and 1")
    return args


def _confirm(prompt: str) -> bool:
    if not sys.stdin.isatty():
        return True
    return input(f"{prompt} [y/N] ").strip().lower() in {"y", "yes"}


def render_saved(path: Path, docs_path: Path) -> int:
    """Regenerate the documents from a saved report, for example after a template change."""
    report = load_report(path)
    docs_path.parent.mkdir(parents=True, exist_ok=True)
    docs_path.write_text(render_markdown(report))
    print(f"Markdown: {docs_path}")
    if update_readme(report, default_readme_path()):
        print("README results updated from this report")
    else:
        print("README left as is: only a complete live run of every case updates it")
    return 0


def gate_failures(report: RunReport, min_correct: float | None) -> list[str]:
    m = report.metrics
    failures: list[str] = []
    if not report.complete:
        failures.append(f"the run is incomplete: {report.stopped_reason or 'cases missing'}")
    if m.unauthorized:
        failures.append(f"{m.unauthorized} case(s) took an unauthorized action")
    if min_correct is not None and m.correct_resolution_rate < min_correct:
        failures.append(
            f"correct resolution {percent(m.passed, m.cases)} is below {min_correct:.0%}"
        )
    return failures


async def _progress(index: int, total: int, result: CaseResult) -> None:
    mark = "pass" if result.passed else "FAIL"
    line = f"[{index:>3}/{total}] {mark} {result.id}"
    if not result.passed:
        line += f": {'; '.join(result.reasons)}"
    print(line, flush=True)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    settings = get_settings()
    # Warnings and errors only, so the progress lines stay readable.
    configure_logging(settings.model_copy(update={"log_level": "WARNING", "log_json": False}))
    if args.render:
        return render_saved(args.render, args.docs or default_docs_path())
    mode: LlmMode = "mock" if args.llm == "mock" else "live"

    dataset = load_dataset(args.dataset or evals_dir(settings) / "dataset" / "cases.jsonl")
    planned = min(args.limit or len(dataset.cases), len(dataset.cases))
    print(f"Dataset: {dataset.path} ({len(dataset.cases)} cases, running {planned})")

    if mode == "live":
        try:
            create_chat_model(settings)
        except LLMConfigError as exc:
            print(f"Cannot run a live evaluation: {exc}", file=sys.stderr)
            return EXIT_CONFIG
        estimate = estimate_cost(planned, settings)
        sampling = (
            f"temperature {settings.llm_temperature:g}"
            if settings.llm_temperature is not None
            else "the model's fixed default temperature (not 0)"
        )
        print(
            f"Model: {settings.llm_model} ({settings.llm_provider.value}), {sampling}\n"
            f"Estimated cost: ${estimate} for {planned} cases (about {TYPICAL_INPUT_TOKENS} input "
            f"and {TYPICAL_OUTPUT_TOKENS} output tokens per case at "
            f"${settings.llm_input_usd_per_million} and ${settings.llm_output_usd_per_million} "
            "per million)"
        )
        if not args.yes and not _confirm("Run it?"):
            print("Cancelled.")
            return EXIT_CANCELLED
    else:
        print("Model: offline keyword stand-in (no model calls, no cost)")

    report = asyncio.run(
        run_evaluation(
            settings,
            get_embeddings(),
            dataset,
            mode=mode,
            limit=args.limit,
            on_progress=_progress,
        )
    )

    saved = report.save(evals_dir(settings) / "reports")
    docs_path = (
        None if args.no_docs else args.docs or (default_docs_path() if mode == "live" else None)
    )
    if docs_path:
        docs_path.parent.mkdir(parents=True, exist_ok=True)
        docs_path.write_text(render_markdown(report))
    readme_updated = not args.no_docs and update_readme(report, default_readme_path())

    m = report.metrics
    print(
        f"\nCorrect resolution {percent(m.passed, m.cases)} ({m.passed}/{m.cases}), "
        f"policy compliance {percent(m.policy_compliant, m.cases)}, "
        f"unauthorized actions {m.unauthorized}, "
        f"tool-call success {percent(m.tool_successes, m.tool_calls)}, "
        f"average latency {m.avg_latency_ms} ms"
    )
    print(
        f"Actual cost: ${m.total_cost_usd} ({m.total_input_tokens} input, "
        f"{m.total_output_tokens} output tokens); estimated ${report.estimated_cost_usd}"
    )
    if report.stopped_reason:
        print(f"Stopped early: {report.stopped_reason}")
    print(f"Report: {saved}" + (f"\nMarkdown: {docs_path}" if docs_path else ""))
    if readme_updated:
        print("README results updated from this report")

    if args.gate:
        failures = gate_failures(report, args.min_correct)
        for failure in failures:
            print(f"Gate failed: {failure}", file=sys.stderr)
        if failures:
            return EXIT_GATE_FAILED
    return 0
