"""Run an agentic benchmark (BrowseComp, DeepSearchQA) with one search provider per profile."""

import argparse
import asyncio
import json
import os
from pathlib import Path

import aiohttp

from agents import grade, loop
from agents.tools import Tools
from providers import search as search_provider
from utils import load_env, now, read_jsonl, write_json

GRADERS = {"exact": grade.exact_answer, "items": grade.answer_set}


async def one_task(session, key, question, profile, args, run_dir):
    tools = Tools(
        session,
        profile,
        cache_dir=run_dir / "searches" / profile["id"],
        page_cache=run_dir / "pages",
        results=profile.get("max_results", 10),
    )
    try:
        outcome = await loop.run(
            session,
            key,
            question["question"],
            tools,
            model=args.model,
            turns=args.turns,
            effort=args.effort,
        )
    except Exception as error:
        return {
            "question_id": question["id"],
            "profile_id": profile["id"],
            "status": "agent_error",
            "error": type(error).__name__,
            "score": 0.0,
        }

    scored = await GRADERS[args.grader](
        session,
        key,
        args.grader_model,
        question["question"],
        outcome["answer"],
        question.get("answer"),
    )
    return {
        "question_id": question["id"],
        "profile_id": profile["id"],
        "status": "ok",
        "answer": outcome["answer"],
        **{k: v for k, v in outcome.items() if k != "answer"},
        **scored,
    }


async def regrade(args, questions, profiles, run_dir):
    """Re-score stored answers after a grader change, with no agent runs."""
    load_env(args.env_file)
    key = os.environ["OPENROUTER_API_KEY"]
    asked = {question["id"]: question for question in questions}
    rows = read_jsonl(run_dir / "answers.jsonl")

    async with aiohttp.ClientSession() as session:
        for row in rows:
            question = asked.get(row["question_id"])
            if not question or row.get("status") != "ok":
                continue
            row.update(
                await GRADERS[args.grader](
                    session,
                    key,
                    args.grader_model,
                    question["question"],
                    row.get("answer"),
                    question.get("answer"),
                )
            )

    (run_dir / "answers.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    )
    return report(run_dir, questions, profiles, args)


async def execute(args):
    load_env(args.env_file)
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")

    questions = read_jsonl(args.questions)[: args.limit]
    config = json.loads(Path(args.config).read_text())
    profiles = config["profiles"]
    for profile in profiles:
        search_provider.validate_profile(profile)

    run_dir = Path(args.run)
    run_dir.mkdir(parents=True, exist_ok=True)
    # The run keeps its own copy of what it ran, so an export never depends on the caller.
    (run_dir / "questions.jsonl").write_text(
        "".join(json.dumps(q, ensure_ascii=False) + "\n" for q in questions)
    )
    (run_dir / "config.json").write_text(json.dumps(config, indent=2) + "\n")
    answers = run_dir / "answers.jsonl"
    done = {
        (row["profile_id"], row["question_id"])
        for row in (read_jsonl(answers) if answers.exists() else [])
    }

    if args.regrade:
        return await regrade(args, questions, profiles, run_dir)
    if args.report_only:
        return report(run_dir, questions, profiles, args)

    semaphore = asyncio.Semaphore(args.concurrency)
    writing = asyncio.Lock()

    async with aiohttp.ClientSession() as session:

        async def guarded(question, profile):
            async with semaphore:
                row = await one_task(session, key, question, profile, args, run_dir)
            # Written as each task lands, so an interrupted run keeps what it paid for.
            async with writing:
                with open(answers, "a") as out:
                    out.write(json.dumps(row, ensure_ascii=False) + "\n")
            return row

        # Question-major order interleaves providers, so every provider progresses at
        # once and no single rate limit gates the whole run.
        pending = [
            (question, profile)
            for question in questions
            for profile in profiles
            if (profile["id"], question["id"]) not in done
        ]
        await asyncio.gather(*(guarded(q, p) for q, p in pending))

    return report(run_dir, questions, profiles, args)


def report(run_dir: Path, questions, profiles, args):
    rows = read_jsonl(run_dir / "answers.jsonl")
    summary = {
        "questions": len(questions),
        "generated_at": now(),
        "model": args.model,
        "grader_model": args.grader_model,
        "grader": args.grader,
        "turns": args.turns,
        "profiles": {},
    }
    for profile in profiles:
        mine = [row for row in rows if row["profile_id"] == profile["id"]]
        scored = [row for row in mine if row.get("status") == "ok"]
        listed = profile.get("price_per_1k_searches_usd")
        summary["profiles"][profile["id"]] = {
            "answered": len(scored),
            "score": sum(row.get("score", 0) for row in mine) / max(len(questions), 1),
            "searches_per_task": _mean(row.get("searches") for row in scored),
            "turns_per_task": _mean(row.get("turns") for row in scored),
            "seconds_per_task": _mean(row.get("seconds") for row in scored),
            "search_seconds_per_call": _search_latency(run_dir, profile["id"]),
            "model_cost_per_task_usd": _mean(
                row.get("model_cost_usd") or 0 for row in scored
            ),
            "search_cost_per_task_usd": _mean(
                _search_cost(row, listed) for row in scored
            ),
            "cost_per_task_usd": _mean(
                (row.get("model_cost_usd") or 0) + (_search_cost(row, listed) or 0)
                for row in scored
            ),
            "ungraded": sum(1 for row in mine if row.get("graded") is False),
            "search_errors": sum(row.get("search_errors") or 0 for row in mine),
            "agent_errors": sum(
                1 for row in mine if row.get("status") == "agent_error"
            ),
        }
    write_json(run_dir / "report.json", summary)
    return summary


def _search_latency(run_dir: Path, profile_id: str) -> float:
    """Mean provider latency per search, from every call cached for this profile."""
    seconds = []
    for path in (run_dir / "searches" / profile_id).glob("*.json"):
        value = json.loads(path.read_text()).get("elapsed_seconds")
        if isinstance(value, (int, float)):
            seconds.append(value)
    return round(sum(seconds) / len(seconds), 3) if seconds else 0.0


def _search_cost(row, listed):
    """Reported search spend, or the provider's list price where it reports none."""
    if isinstance(row.get("search_cost_usd"), (int, float)) and row["search_cost_usd"]:
        return row["search_cost_usd"]
    if listed and isinstance(row.get("searches"), int):
        return row["searches"] * listed / 1000
    return 0.0


def _mean(values):
    values = [value for value in values if isinstance(value, (int, float))]
    return round(sum(values) / len(values), 4) if values else 0.0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", required=True)
    parser.add_argument(
        "--config", required=True, help="Search profiles, as in configs/search.json"
    )
    parser.add_argument(
        "--run", required=True, help="Run directory: caches, answers and report"
    )
    parser.add_argument("--grader", choices=sorted(GRADERS), default="exact")
    parser.add_argument("--model", default="openai/gpt-5.6-luna")
    parser.add_argument("--grader-model", default="openai/gpt-5.4-mini")
    parser.add_argument("--effort", default="medium")
    parser.add_argument("--turns", type=int, default=25)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument(
        "--regrade", action="store_true", help="Re-score stored answers, no agent runs"
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="Rebuild the report from stored answers",
    )
    args = parser.parse_args()
    print(json.dumps(asyncio.run(execute(args)), indent=2))


if __name__ == "__main__":
    main()
