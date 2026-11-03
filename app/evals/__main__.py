"""Command line: run the evals for one prompt version, or compare two saved results.

    uv run python -m app.evals run --prompt-version v1 --repeats 3 --out evals/results/v1.json
    uv run python -m app.evals run --prompt-version v2 --repeats 3 --out evals/results/v2.json
    uv run python -m app.evals compare evals/results/v1.json evals/results/v2.json

`run` calls the real model APIs (GEMINI_API_KEY / OPENAI_API_KEY) and spends money, so it has a
hard cost cap (--max-cost, default $2.00). Output is markdown, ready to paste into the README.
"""

import argparse
import asyncio
import sys
from pathlib import Path

from app.config import Settings
from app.evals.datasets import DatasetError, load_answers, load_injection, load_pages
from app.evals.models import EvalResult
from app.evals.report import render_comparison, render_markdown
from app.evals.runner import run_eval
from app.extract.extractor import Generator
from app.extract.prompts import PROMPTS_DIR
from app.llm.client import LLMClient
from app.llm.cost import CostTracker

DEFAULT_DIR = Path("evals")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.evals", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run the eval suites for one prompt version")
    run.add_argument(
        "--prompt-version", required=True, help="e.g. v1 or v2 (prompts/extract_<v>.txt)"
    )
    run.add_argument("--repeats", type=int, default=3)
    run.add_argument("--provider", default="gemini")
    run.add_argument("--model", default="gemini-3.8-flash")
    run.add_argument("--answers", type=Path, default=DEFAULT_DIR / "answers.json")
    run.add_argument("--pages", type=Path, default=DEFAULT_DIR / "pages.json")
    run.add_argument("--injection", type=Path, default=DEFAULT_DIR / "injection.json")
    run.add_argument("--max-cost", type=float, default=2.0, help="hard USD cap for the whole run")
    run.add_argument("--out", type=Path, help="also write the full JSON result here")
    run.add_argument("--save", action="store_true", help="store the result in the database")

    compare = sub.add_parser("compare", help="compare two saved results (baseline, candidate)")
    compare.add_argument("baseline", type=Path)
    compare.add_argument("candidate", type=Path)
    return parser


async def _run(args: argparse.Namespace) -> int:
    try:
        answers = load_answers(args.answers)
        pages = load_pages(args.pages)
        injection = load_injection(args.injection)
    except DatasetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not (PROMPTS_DIR / f"extract_{args.prompt_version}.txt").is_file():
        print(f"error: no prompt file prompts/extract_{args.prompt_version}.txt", file=sys.stderr)
        return 2

    tracker = CostTracker(cap_usd=args.max_cost)  # one cap for the whole run, all repeats

    def factory() -> Generator:
        return LLMClient.from_env(cost_cap_usd=args.max_cost, tracker=tracker)

    result = await run_eval(
        llm_factory=factory,
        provider=args.provider,
        model=args.model,
        prompt_version=args.prompt_version,
        repeats=args.repeats,
        answers=answers,
        pages=pages,
        injection=injection,
    )
    print(render_markdown(result))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(result.model_dump_json(indent=2), encoding="utf8")
    if args.save:
        from app.evals.store import save_eval_result

        await save_eval_result(Settings.from_env().database_url, result)
        print("saved to the database", file=sys.stderr)
    return 0


def _compare(args: argparse.Namespace) -> int:
    baseline = EvalResult.model_validate_json(args.baseline.read_text(encoding="utf8"))
    candidate = EvalResult.model_validate_json(args.candidate.read_text(encoding="utf8"))
    print(render_comparison(baseline, candidate))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "compare":
        return _compare(args)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
