"""Measure judge accuracy against human labels: sample judged cases blind, optionally pre-label them, then score."""

import argparse
import asyncio
import json
import os
import random
from collections import defaultdict
from pathlib import Path

import aiohttp

from evaluators import answer_match, judge
from utils import digest, load_env, read_jsonl, write_json

STRATA = ("accepted", "unsupported", "rejected_with_match", "rejected_without_match")
PRELABEL = """You label test cases for a search benchmark. Each case has a question, its
verified answer with accepted aliases, and a source: its title, publication date if known,
and text. Decide whether the source states the verified answer for exactly what the question
asks: same entity, event and relation. If the title states the answer, the source states
it. A date or period that only identifies the event in the question does not need to be
restated when the source clearly reports that same event; a date that is itself the
requested answer must be stated. The same value in another context or for a different
event does not count. Use no outside knowledge.
Return JSON: {"label":"yes"|"no","quote":"exact words from the text that state the
answer, or empty","reason":"one sentence"}"""


def stratum(basis: dict) -> str | None:
    if basis.get("status") == "unsupported_answer":
        return "unsupported"
    if basis.get("status") != "ok" or not basis.get("judgment"):
        return None
    if basis.get("hit"):
        return "accepted"
    return (
        "rejected_with_match"
        if basis.get("screen") != "none"
        else "rejected_without_match"
    )


def judged_cases(results: dict) -> dict[str, dict]:
    """One case per distinct judged text, remembering every provider that returned it."""
    cases = {}
    for profile, rows in results["profiles"].items():
        for row in rows:
            for result in row["results"]:
                for name in ("page", "snippet"):
                    basis = result.get(name) or {}
                    group = stratum(basis)
                    if group is None:
                        continue
                    key = basis["evidence"]
                    case = cases.setdefault(
                        key,
                        {
                            "question_id": row["question_id"],
                            "evidence": key,
                            "stratum": group,
                            "judge_hit": basis["hit"],
                            "judge_label": basis["judgment"]["label"],
                            "profiles": set(),
                        },
                    )
                    case["profiles"].add(profile)
    return cases


def sample(args) -> None:
    evaluation = Path(args.evaluation)
    results = json.loads((evaluation / "results.json").read_text())
    questions = {q["id"]: q for q in read_jsonl(evaluation.parent / "questions.jsonl")}
    cases = judged_cases(results)
    by_stratum = defaultdict(list)
    for key, case in sorted(cases.items()):
        by_stratum[case["stratum"]].append(key)
    rng = random.Random(args.seed)
    for keys in by_stratum.values():
        rng.shuffle(keys)
    picked, share = [], args.n // len(STRATA)
    for group in STRATA:
        picked += by_stratum[group][:share]
    leftovers = [k for g in STRATA for k in by_stratum[g][share:]]
    rng.shuffle(leftovers)
    picked += leftovers[: args.n - len(picked)]
    rng.shuffle(picked)

    blind, key_file = [], {}
    for number, key in enumerate(picked, 1):
        case = cases[key]
        question = questions[case["question_id"]]
        evidence = results["evidence"][key]
        case_id = f"c{number:04d}"
        blind.append(
            {
                "case_id": case_id,
                "question": question["question"],
                "verified_answer": question["answer"],
                "accepted_aliases": question.get("answer_aliases", []),
                "title": evidence["title"],
                "published": evidence.get("published"),
                "text": evidence["text"],
                "human_label": None,
                "labeler": None,
                "note": "",
            }
        )
        key_file[case_id] = {**case, "profiles": sorted(case["profiles"])}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in blind))
    write_json(
        out.with_suffix(".key.json"),
        {
            "stratum_sizes": {g: len(by_stratum[g]) for g in STRATA},
            "cases": key_file,
        },
    )
    print(
        f"{len(blind)} blind cases -> {out}; stratum sizes {dict((g, len(by_stratum[g])) for g in STRATA)}"
    )


async def prelabel(args) -> None:
    load_env(args.env_file)
    key = os.environ.get("OPENROUTER_API_KEY", "")
    path = Path(args.cases)
    cases = read_jsonl(path)
    cache = path.parent / "prelabel-calls"
    semaphore = asyncio.Semaphore(args.concurrency)
    async with aiohttp.ClientSession() as session:

        async def one(case):
            data = {
                k: case.get(k)
                for k in (
                    "question",
                    "verified_answer",
                    "accepted_aliases",
                    "title",
                    "published",
                    "text",
                )
            }
            messages = [
                {"role": "system", "content": PRELABEL},
                {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
            ]
            target = (
                cache / f"{digest({'model': args.model, 'messages': messages})}.json"
            )
            if target.exists():
                trace = json.loads(target.read_text())
            else:
                async with semaphore:
                    trace = await judge.chat(
                        session, key, args.model, messages, max_tokens=600
                    )
                write_json(target, trace)
            value = judge.parse_json(trace.get("content"))
            label = value.get("label") if value.get("label") in ("yes", "no") else None
            quote = value.get("quote") or ""
            if (
                label == "yes"
                and answer_match.contains(f"{case['title']}\n{case['text']}", quote)
                == "none"
            ):
                label = None
            return {
                **case,
                "suggested_label": label,
                "suggested_quote": quote,
                "suggested_reason": value.get("reason", ""),
                "suggested_by": args.model,
            }

        labelled = await asyncio.gather(*(one(case) for case in cases))
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in labelled)
    )
    unresolved = sum(row["suggested_label"] is None for row in labelled)
    print(
        f"pre-labelled {len(labelled)} cases with {args.model}; {unresolved} left for manual review"
    )


def score(cases: list[dict], key: dict) -> dict:
    """Judge agreement with human labels, per stratum and weighted by stratum size across the whole run."""
    per = defaultdict(lambda: {"labelled": 0, "human_yes": 0, "agree": 0})
    for case in cases:
        if case.get("human_label") not in ("yes", "no"):
            continue
        truth = case["human_label"] == "yes"
        info = key["cases"][case["case_id"]]
        cell = per[info["stratum"]]
        cell["labelled"] += 1
        cell["human_yes"] += truth
        cell["agree"] += truth == bool(info["judge_hit"])
    sizes = key["stratum_sizes"]

    def rate(group, field):
        cell = per[group]
        return cell[field] / cell["labelled"] if cell["labelled"] else None

    rejected = ("unsupported", "rejected_with_match", "rejected_without_match")
    known = [g for g in rejected if rate(g, "human_yes") is not None]
    missed = sum(sizes[g] * rate(g, "human_yes") for g in known)
    rejected_total = sum(sizes[g] for g in known)
    precision = rate("accepted", "human_yes")
    accepted_true = sizes["accepted"] * precision if precision is not None else None
    return {
        "labelled": sum(cell["labelled"] for cell in per.values()),
        "per_stratum": {g: {**per[g], "size": sizes[g]} for g in STRATA},
        "precision_of_hits": precision,
        "share_of_rejections_that_answer": missed / rejected_total
        if rejected_total
        else None,
        "recall_of_hits": (
            accepted_true / (accepted_true + missed)
            if accepted_true is not None and accepted_true + missed
            else None
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    s = commands.add_parser("sample")
    s.add_argument("--evaluation", required=True, help="Directory holding results.json")
    s.add_argument("--out", required=True)
    s.add_argument("--n", type=int, default=200)
    s.add_argument("--seed", type=int, default=7)
    p = commands.add_parser("prelabel")
    p.add_argument("--cases", required=True)
    p.add_argument("--model", default="google/gemini-3.8-flash")
    p.add_argument("--concurrency", type=int, default=8)
    p.add_argument("--env-file", default=".env")
    c = commands.add_parser("score")
    c.add_argument("--cases", required=True)
    c.add_argument("--out", required=True)
    args = parser.parse_args()
    if args.command == "sample":
        sample(args)
    elif args.command == "prelabel":
        asyncio.run(prelabel(args))
    else:
        cases = read_jsonl(args.cases)
        key = json.loads(Path(args.cases).with_suffix(".key.json").read_text())
        report = score(cases, key)
        write_json(args.out, report)
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
