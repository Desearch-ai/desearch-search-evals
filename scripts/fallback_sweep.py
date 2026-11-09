"""Simulate falling back to a second provider when the base profile's results look weak, from saved searches only."""

import argparse
import asyncio
import json
import os
import re
from datetime import date
from pathlib import Path

import aiohttp

from evaluators.answer_match import normalize
from evaluators.grade import is_mirror, url_key
from scripts import run_answers
from utils import read_jsonl

STOP = set(
    "what which who whom whose when where were with that this from have has had into than then them they their there these those about after before would could should does many much most more being been also only over under between during year years name".split()
)
SPLIT = "{base}+{fallback}"


def terms(text):
    return {w for w in normalize(text).split() if len(w) > 3 and w not in STOP}


def coverage(question, search, top=5):
    """Best share of the question's content words found in one of the base's top results."""
    wanted = terms(question)
    rows = [r for r in (search.get("results") or [])[:top] if not is_mirror(r)]
    if not wanted or not rows:
        return 0.0
    return max(
        len(wanted & terms(f"{r.get('title') or ''} {r.get('text') or ''}"))
        / len(wanted)
        for r in rows
    )


def confidence(response):
    found = re.findall(r"Confidence:\s*(\d+(?:\.\d+)?)", response or "")
    return float(found[-1]) / 100 if found else 0.0


def merged(base, fallback, each=5):
    rows, seen = [], set()
    for row in [
        *(base.get("results") or [])[:each],
        *(fallback.get("results") or [])[:each],
    ]:
        key = url_key(row.get("url") or "")
        if key in seen:
            continue
        seen.add(key)
        rows.append({**row, "rank": len(rows) + 1})
    return {"results": rows}


def load(run, profile, qid):
    path = Path(run) / "searches" / profile / f"{qid}.json"
    return json.loads(path.read_text()) if path.exists() else {"error": "missing"}


async def answer(args):
    run_answers.load_env(args.env_file)
    run = Path(args.run)
    questions = read_jsonl(args.questions)
    name = SPLIT.format(base=args.base, fallback=args.fallback)
    out = run / "answers"
    answers = run_answers.Store(out / "fallback_answers.jsonl")
    grades = run_answers.Store(out / "fallback_grades.jsonl")
    token, key = run_answers.chutes_token(), os.environ["OPENROUTER_API_KEY"]
    today = args.today or date.today().isoformat()
    slots = asyncio.Semaphore(args.concurrency)
    spend = {"grader": 0.0, "reader": 0.0}

    async def one(session, q):
        item = f"{name}/{q['id']}"
        async with slots:
            if item not in answers.rows:
                ctx = run_answers.context(
                    merged(
                        load(run, args.base, q["id"]), load(run, args.fallback, q["id"])
                    )
                )
                text, usage = await run_answers.read(
                    session, token, q["question"], ctx, today, args.reader_tokens
                )
                spend["reader"] += usage["usd"]
                answers.put(
                    {
                        "key": item,
                        "question_id": q["id"],
                        "response": text,
                        "usage": usage,
                    }
                )
            if item not in grades.rows:
                verdict, reply, cost = await run_answers.grade(
                    session,
                    key,
                    q["benchmark"],
                    q["question"],
                    q["answer"],
                    answers.rows[item]["response"],
                )
                spend["grader"] += cost or 0.0
                grades.put(
                    {
                        "key": item,
                        "question_id": q["id"],
                        "grade": verdict,
                        "reply": reply,
                        "cost_usd": cost,
                    }
                )

    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(
            *(one(session, q) for q in questions), return_exceptions=True
        )
    failed = [r for r in results if isinstance(r, Exception)]
    print(
        f"{name}: {len(grades.rows)} graded, {len(failed)} failed, reader ${spend['reader']:.2f}, grader ${spend['grader']:.2f}"
    )


def sweep(args):
    run = Path(args.run)
    questions = read_jsonl(args.questions)
    out = run / "answers"
    grades = {r["key"]: r["grade"] for r in read_jsonl(out / "grades.jsonl")}
    answers = {r["key"]: r["response"] for r in read_jsonl(out / "answers.jsonl")}
    fb = {r["key"]: r["grade"] for r in read_jsonl(out / "fallback_grades.jsonl")}
    name = SPLIT.format(base=args.base, fallback=args.fallback)
    rows = []
    for q in questions:
        rows.append(
            {
                "id": q["id"],
                "coverage": coverage(q["question"], load(run, args.base, q["id"])),
                "confidence": confidence(answers.get(f"{args.base}/{q['id']}")),
                "base": grades[f"{args.base}/{q['id']}"] == "CORRECT",
                "octen": grades[f"{args.fallback}/{q['id']}"] == "CORRECT",
                "merged": fb[f"{name}/{q['id']}"] == "CORRECT",
            }
        )
    n = len(rows)
    lines = [
        f"### {args.label}: fall back to {args.fallback} when {args.base} looks weak ({n} questions)",
        "",
        f"{args.base} alone {sum(r['base'] for r in rows) / n:.1%}; {args.fallback} alone {sum(r['octen'] for r in rows) / n:.1%}; merged top 5 + top 5 on every question {sum(r['merged'] for r in rows) / n:.1%}.",
        "",
        "| Signal | Fallback below | Fallback rate | (a) fallback-only accuracy | gained / lost | (b) merged accuracy | gained / lost | Fallback searches per 1k queries ($) |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for signal in ("coverage", "confidence"):
        values = sorted(r[signal] for r in rows)
        cuts = sorted({values[min(n - 1, int(n * share))] for share in args.rates})
        for cut in [*cuts, float("inf")]:
            fall = [r for r in rows if r[signal] < cut]
            stay = [r for r in rows if r[signal] >= cut]
            line = [
                signal,
                "all" if cut == float("inf") else f"{cut:.2f}",
                f"{len(fall) / n:.0%}",
            ]
            for variant in ("octen", "merged"):
                hits = sum(r["base"] for r in stay) + sum(r[variant] for r in fall)
                gained = sum(r[variant] and not r["base"] for r in fall)
                lost = sum(r["base"] and not r[variant] for r in fall)
                line += [f"{hits / n:.1%}", f"+{gained} / -{lost}"]
            line.append(
                f"{len(fall) / n * 1000:.0f} (${len(fall) / n * 1000 * args.price:.2f})"
            )
            lines.append("| " + " | ".join(line) + " |")
    lines += [
        "",
        "Coverage is the best share of the question's content words in one of our top 5 results; confidence is the reader's own stated confidence on our results. Neither uses the answer. Gained / lost count questions against our profile alone.",
        "",
    ]
    text = "\n".join(lines)
    if args.out:
        Path(args.out).write_text(text)
    print(text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["answer", "sweep"])
    parser.add_argument("--questions", required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--base", default="desearch-index")
    parser.add_argument("--fallback", default="octen")
    parser.add_argument("--label", default="")
    parser.add_argument(
        "--price", type=float, default=0.001, help="fallback cost per search"
    )
    parser.add_argument(
        "--rates", type=float, nargs="+", default=[0.1, 0.2, 0.3, 0.5, 0.7]
    )
    parser.add_argument("--today")
    parser.add_argument("--reader-tokens", type=int, default=6000)
    parser.add_argument("--concurrency", type=int, default=24)
    parser.add_argument("--out")
    parser.add_argument("--env-file", default=".env")
    args = parser.parse_args()
    if args.command == "answer":
        asyncio.run(answer(args))
    else:
        sweep(args)


if __name__ == "__main__":
    main()
