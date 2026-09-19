import argparse
import asyncio
import json
import math
import os
import random
import re
from pathlib import Path

import aiohttp

import utils
from evaluators.sources import normalize_sources
from providers import search
from providers.desearch import load_local
from utils import load_env, now, read_jsonl, write_json


def valid_cost(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def validate_options(batch_size, concurrency):
    if type(batch_size) is not int or batch_size < 1:
        raise ValueError("Batch size must be a positive integer")
    if type(concurrency) is not int or not 1 <= concurrency <= 32:
        raise ValueError("Concurrency must be an integer between 1 and 32")


def validate_inputs(questions, config):
    if not isinstance(questions, list) or not questions:
        raise ValueError("Questions must be a nonempty list")
    if not isinstance(config, dict) or not isinstance(config.get("profiles"), list):
        raise TypeError("Config must contain a profiles list")
    profiles = config["profiles"]
    for item in [*questions, *profiles]:
        identifier = item.get("id") if isinstance(item, dict) else None
        if not isinstance(identifier, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]+", identifier
        ):
            raise ValueError("IDs must be safe artifact filenames")
    if len({q["id"] for q in questions}) != len(questions):
        raise ValueError("Questions must have unique IDs")
    if not profiles or len({p["id"] for p in profiles}) != len(profiles):
        raise ValueError("Profiles must be nonempty and have unique IDs")
    for question in questions:
        if (
            not isinstance(question.get("question"), str)
            or not question["question"].strip()
        ):
            raise ValueError("Question text must be nonempty")
    for profile in profiles:
        search.validate_profile(profile)


def load_inputs(args):
    load_env(args.env_file)
    validate_options(args.batch_size, args.concurrency)
    config = json.loads(Path(args.config).read_text())
    questions = read_jsonl(args.questions)
    validate_inputs(questions, config)
    selected = set(args.profiles.split(",")) if args.profiles else None
    if selected:
        if selected - {p["id"] for p in config["profiles"]}:
            raise ValueError("Requested profiles are absent from the config")
        config = {
            **config,
            "profiles": [p for p in config["profiles"] if p["id"] in selected],
        }
    if args.limit is not None:
        if type(args.limit) is not int or args.limit < 1:
            raise ValueError("Question limit must be a positive integer")
        questions = questions[: args.limit]
    return questions, config


def check_run(run, questions, config):
    question_path, config_path = run / "questions.jsonl", run / "config.json"
    if question_path.exists() != config_path.exists():
        raise ValueError("Saved run inputs are incomplete; use a new run directory")
    if question_path.exists():
        if (
            read_jsonl(question_path) != questions
            or json.loads(config_path.read_text()) != config
        ):
            raise ValueError(
                "Saved questions or config changed; use a new run directory"
            )
    elif run.exists() and any(run.iterdir()):
        raise ValueError("Output directory has no matching saved questions/config")


def prepare(run, questions, config):
    run = Path(run)
    validate_inputs(questions, config)
    check_run(run, questions, config)
    if not (run / "questions.jsonl").exists():
        write_json(run / "config.json", config)
        (run / "questions.jsonl").write_text(
            "".join(json.dumps(q, ensure_ascii=False) + "\n" for q in questions)
        )


def validate_result(row, question, profile):
    if not isinstance(row, dict) or row.get("query") != question["question"]:
        raise ValueError("Search query differs from the frozen question")
    if (
        row.get("question_id") != question["id"]
        or row.get("profile_id") != profile["id"]
    ):
        raise ValueError("Search artifact question/profile IDs do not match its path")
    if row.get("status") not in {"ok", "error"}:
        raise ValueError("Search artifact needs an explicit ok/error status")
    normalize_sources(row)
    for source in row.get("results", []):
        if not isinstance(source, dict):
            raise TypeError("Search results must be objects")
        for field in ("title", "text", "url"):
            if source.get(field) is not None and not isinstance(source[field], str):
                raise ValueError(f"Search result {field} must be text")


def preflight(args, questions, config):
    run = Path(args.run)
    check_run(run, questions, config)
    imported = (
        load_local(args.import_local, questions, config["profiles"])
        if args.import_local
        else {}
    )
    expected = {
        f"{p['id']}/{q['id']}.json" for q in questions for p in config["profiles"]
    }
    for path in (run / "searches").glob("*/*.json"):
        if str(path.relative_to(run / "searches")) not in expected:
            raise ValueError(
                "Search artifacts include questions/profiles outside this run"
            )
    missing = []
    for question in questions:
        for profile in config["profiles"]:
            relative = f"{profile['id']}/{question['id']}.json"
            target = run / "searches" / relative
            row = imported.get(relative)
            if target.exists():
                cached = json.loads(target.read_text())
                if row is not None and row != cached:
                    raise ValueError(
                        "Local result would overwrite a different artifact"
                    )
                row = cached
            if row is not None:
                validate_result(row, question, profile)
            elif profile["transport"] == "local":
                raise ValueError(
                    f"Missing local Desearch result {relative}; provide a complete --import-local JSONL file"
                )
            else:
                missing.append((question, profile))
    needed = {
        "TAVILY_API_KEY" if p["transport"] == "tavily" else "OPENROUTER_API_KEY"
        for _, p in missing
    }
    for name in sorted(needed):
        if not os.environ.get(name, "").strip():
            raise ValueError(f"{name} is required for uncached searches")
    return imported, missing


async def execute(args, prepared=None):
    if prepared is None:
        questions, config = load_inputs(args)
        imported, missing = preflight(args, questions, config)
    else:
        questions, config, imported, missing = prepared
    run = Path(args.run)
    prepare(run, questions, config)
    for relative, row in imported.items():
        target = run / "searches" / relative
        if not target.exists():
            write_json(target, row)
    semaphore = asyncio.Semaphore(args.concurrency)
    processed = 0
    async with aiohttp.ClientSession() as session:

        async def task(item):
            nonlocal processed
            question, profile = item
            identifier = question["id"]
            target = run / "searches" / profile["id"] / f"{identifier}.json"
            async with semaphore:
                try:
                    row = await search.search(session, question["question"], profile)
                    if not isinstance(row, dict):
                        raise TypeError("Search adapter returned a non-object")
                except (
                    aiohttp.ClientError,
                    TimeoutError,
                    ValueError,
                    TypeError,
                    KeyError,
                    RuntimeError,
                ) as error:
                    row = {
                        "status": "error",
                        "error": type(error).__name__,
                        "results": [],
                        "cost_usd": None,
                    }
                row = {
                    **row,
                    "question_id": identifier,
                    "profile_id": profile["id"],
                    "completed_at": now(),
                }
                row.setdefault("query", question["question"])
                row.setdefault("status", "error" if row.get("error") else "ok")
                try:
                    validate_result(row, question, profile)
                except (ValueError, TypeError) as error:
                    row = {
                        **row,
                        "invalid_result": dict(row),
                        "status": "error",
                        "error": f"Invalid search result: {error}",
                        "results": [],
                        "query": question["question"],
                    }
                write_json(target, row)
                processed += 1
                if row.get("error"):
                    print(
                        f"Search failed: {profile['id']}/{identifier}: {row['error']}",
                        flush=True,
                    )

        random.Random(0).shuffle(missing)
        await utils.map_batches(missing, task, args.batch_size)
    artifacts = [json.loads(p.read_text()) for p in (run / "searches").glob("*/*.json")]
    print(f"Searches: {len(artifacts)} saved, {processed} new", flush=True)
    recorded = sum(
        row["cost_usd"] for row in artifacts if valid_cost(row.get("cost_usd"))
    )
    unknown = sum(not valid_cost(row.get("cost_usd")) for row in artifacts)
    return {
        "cost_usd": None if unknown else recorded,
        "recorded_cost_usd": recorded,
        "unknown_cost_searches": unknown,
        "processed": processed,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--questions", required=True)
    parser.add_argument("--config", default="configs/search.json")
    parser.add_argument("--run", required=True)
    parser.add_argument("--import-local")
    parser.add_argument("--profiles")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--env-file", default=".env")
    try:
        asyncio.run(execute(parser.parse_args()))
    except (ValueError, TypeError, OSError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
