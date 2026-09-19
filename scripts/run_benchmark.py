import argparse
import asyncio
import os
from pathlib import Path

from evaluators import grade, source_report
from providers import run_search


async def execute(args):
    run = Path(args.run).resolve()
    search_args = argparse.Namespace(**vars(args), profiles=None, limit=None)
    questions, config = run_search.load_inputs(search_args)
    imported, missing = run_search.preflight(search_args, questions, config)
    if missing and not os.environ.get("OPENROUTER_API_KEY", "").strip():
        raise ValueError("OPENROUTER_API_KEY is required for grading")
    await run_search.execute(search_args, (questions, config, imported, missing))

    evaluation = run / "evaluation"
    await grade.execute(
        argparse.Namespace(
            run=run,
            out=evaluation,
            page_cache=run / "page-cache",
            model=args.judge,
            concurrency=args.concurrency,
            fetch_concurrency=args.fetch_concurrency,
            batch_size=args.batch_size,
            gold_only=args.gold_only,
            env_file=args.env_file,
        )
    )
    report = source_report.write_report(evaluation)
    print(f"Benchmark complete: {evaluation / 'report.md'}", flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(
        description="Search, grade and report one benchmark run."
    )
    parser.add_argument("--questions", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--import-local")
    parser.add_argument("--judge", default=grade.JUDGE)
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument(
        "--gold-only",
        action="store_true",
        help="Score gold URLs only: no page fetching or model calls",
    )
    parser.add_argument("--concurrency", type=int, default=12)
    parser.add_argument("--fetch-concurrency", type=int, default=16)
    parser.add_argument("--env-file", default=".env")
    try:
        asyncio.run(execute(parser.parse_args()))
    except (ValueError, TypeError, RuntimeError, OSError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
