"""Paired accuracy tests that average K reader reads on changed questions and copy unchanged ones, with a shared read bank per benchmark."""

import argparse
import asyncio
import hashlib
import json
import math
import os
import random
from collections import defaultdict
from pathlib import Path

import aiohttp

from scripts import run_answers as ra
from utils import read_jsonl

TODAY = "2026-11-09"


def ctx_hash(ctx, today=TODAY):
    blob = json.dumps([ra.READER, today, ra.READER_SYSTEM, ctx], ensure_ascii=False)
    return hashlib.sha1(blob.encode()).hexdigest()


def search_ctx(run, profile, qid):
    path = Path(run) / "searches" / profile / f"{qid}.json"
    if not path.exists():
        return None
    search = json.loads(path.read_text())
    return ra.context(search) if not search.get("error") else "(search failed)"


class Bank:
    """Reads and grades per (question, context hash); any run with the same context reuses them."""

    def __init__(self, path, grader):
        self.path, self.grader = Path(path), grader
        self.reads = defaultdict(list)
        if self.path.exists():
            for line in self.path.read_text().splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("grader") == grader:
                    self.reads[(row["qid"], row["ctx"])].append(row)

    def add(self, row):
        row = {**row, "grader": self.grader}
        self.reads[(row["qid"], row["ctx"])].append(row)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def scores(self, qid, ctx):
        return [1.0 if r["grade"] == "CORRECT" else 0.0 for r in self.reads[(qid, ctx)]]


def import_run(bank, run, profile, questions, source):
    """Seed the bank from a run's saved answers and single-grader grades, once per source tag."""
    run = Path(run)
    answers = (
        {r["key"]: r for r in read_jsonl(run / "answers" / "answers.jsonl")}
        if (run / "answers" / "answers.jsonl").exists()
        else {}
    )
    gpath = run / "answers" / ra.grades_name()
    grades = {r["key"]: r for r in read_jsonl(gpath)} if gpath.exists() else {}
    have = {
        (r["qid"], r["ctx"], r.get("source"))
        for rows in bank.reads.values()
        for r in rows
    }
    added = 0
    for q in questions:
        key = f"{profile}/{q['id']}"
        if key not in answers or key not in grades:
            continue
        ctx = search_ctx(run, profile, q["id"])
        if ctx is None:
            continue
        h = ctx_hash(ctx)
        if (q["id"], h, source) in have:
            continue
        bank.add(
            {
                "qid": q["id"],
                "ctx": h,
                "source": source,
                "exact": answers[key].get("exact"),
                "grade": grades[key]["grade"],
            }
        )
        added += 1
    return added


async def fill(bank, jobs, k, concurrency=16, cap=5.0, today=TODAY, tokens=6000):
    """Read and grade until every (question, context) job has k reads."""
    token, key = ra.chutes_token(), os.environ.get("OPENROUTER_API_KEY")
    slots = asyncio.Semaphore(concurrency)
    spend = {"reader": 0.0, "grader": 0.0, "reads": 0, "failed": 0}

    async def one(session, q, ctx_text, h):
        if spend["grader"] >= cap:
            return
        async with slots:
            try:
                text, usage = await ra.read(
                    session, token, q["question"], ctx_text, today, tokens
                )
                verdict, reply, cost = await ra.grade(
                    session, key, q["benchmark"], q["question"], q["answer"], text
                )
            except RuntimeError:
                spend["failed"] += 1
                return
        spend["reader"] += usage["usd"]
        spend["grader"] += cost or 0.0
        spend["reads"] += 1
        bank.add(
            {
                "qid": q["id"],
                "ctx": h,
                "source": "kread",
                "exact": ra.exact_answer(text),
                "grade": verdict,
                "usage": usage,
            }
        )

    async with aiohttp.ClientSession() as session:
        tasks = []
        for q, ctx_text in jobs:
            h = ctx_hash(ctx_text, today)
            for _ in range(max(0, k - len(bank.scores(q["id"], h)))):
                tasks.append(one(session, q, ctx_text, h))
        await asyncio.gather(*tasks)
    return spend


def bootstrap(diffs, strata=None, rounds=4000, seed=0):
    """Mean paired difference and its 95% percentile CI, resampling questions within each stratum."""
    rng = random.Random(seed)
    groups = defaultdict(list)
    for i, d in enumerate(diffs):
        groups[strata[i] if strata else 0].append(d)
    n = len(diffs)
    means = []
    for _ in range(rounds):
        total = 0.0
        for values in groups.values():
            total += sum(values[rng.randrange(len(values))] for _ in values)
        means.append(total / n)
    means.sort()
    return sum(diffs) / n, means[int(0.025 * rounds)], means[int(0.975 * rounds) - 1]


def verdict(lo, hi):
    return "win" if lo > 0 else "loss" if hi < 0 else "unresolved"


def paired(bank, questions, base, cand, k):
    """Per-question difference: 0 when the reader context is identical, else mean of k candidate reads minus mean of k baseline reads."""
    rows = []
    for q in questions:
        cb, cc = (
            search_ctx(base[0], base[1], q["id"]),
            search_ctx(cand[0], cand[1], q["id"]),
        )
        if cb is None or cc is None:
            continue
        hb, hc = ctx_hash(cb), ctx_hash(cc)
        if hb == hc:
            rows.append(
                {
                    "qid": q["id"],
                    "benchmark": q["benchmark"],
                    "changed": False,
                    "d": 0.0,
                }
            )
            continue
        sb, sc = bank.scores(q["id"], hb)[:k], bank.scores(q["id"], hc)[:k]
        if not sb or not sc:
            rows.append(
                {
                    "qid": q["id"],
                    "benchmark": q["benchmark"],
                    "changed": True,
                    "d": None,
                }
            )
            continue
        rows.append(
            {
                "qid": q["id"],
                "benchmark": q["benchmark"],
                "changed": True,
                "d": sum(sc) / len(sc) - sum(sb) / len(sb),
                "kb": len(sb),
                "kc": len(sc),
                "single": sc[0] - sb[0],
            }
        )
    return rows


def summarize(rows, label=""):
    used = [r for r in rows if r["d"] is not None]
    missing = len(rows) - len(used)
    diffs = [r["d"] for r in used]
    strata = [r["benchmark"] for r in used]
    est, lo, hi = bootstrap(diffs, strata)
    changed = sum(r["changed"] for r in used)
    single = [r.get("single", 0.0) for r in used]
    s_est, s_lo, s_hi = bootstrap(single, strata, seed=1)
    return {
        "label": label,
        "questions": len(used),
        "changed": changed,
        "missing_reads": missing,
        "estimate": 100 * est,
        "ci": [100 * lo, 100 * hi],
        "verdict": verdict(lo, hi),
        "single_read": {
            "estimate": 100 * s_est,
            "ci": [100 * s_lo, 100 * s_hi],
            "verdict": verdict(s_lo, s_hi),
        },
    }


def aa(bank, questions, run, profile, k, resamples=20, seed=0):
    """A/A: split each question's banked reads of one context into two disjoint arms of k and test the difference."""
    rng = random.Random(seed)
    pools = []
    for q in questions:
        ctx = search_ctx(run, profile, q["id"])
        if ctx is None:
            continue
        scores = bank.scores(q["id"], ctx_hash(ctx))
        if len(scores) >= 2 * k:
            pools.append((q["benchmark"], scores))
    results = []
    for i in range(resamples):
        diffs, strata = [], []
        for bench, scores in pools:
            picked = rng.sample(scores, 2 * k)
            diffs.append(sum(picked[k:]) / k - sum(picked[:k]) / k)
            strata.append(bench)
        est, lo, hi = bootstrap(diffs, strata, rounds=2000, seed=seed + i)
        results.append(
            {
                "estimate": 100 * est,
                "ci": [100 * lo, 100 * hi],
                "verdict": verdict(lo, hi),
            }
        )
    false = sum(r["verdict"] != "unresolved" for r in results)
    return {
        "questions": len(pools),
        "k": k,
        "resamples": resamples,
        "false_decisions": false,
        "rate": false / resamples if resamples else 0,
        "runs": results,
    }


def mde(var, n):
    return 100 * 2.8 * math.sqrt(var / n) if n else float("nan")


def load_questions(path, qids=None):
    questions = read_jsonl(path)
    if qids:
        wanted = {
            l.strip()
            for l in Path(qids).read_text().splitlines()
            if l.strip() and not l.startswith("#")
        }
        questions = [q for q in questions if q["id"] in wanted]
    return questions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("import", "bank", "compare", "aa"):
        p = sub.add_parser(name)
        p.add_argument("--questions", required=True)
        p.add_argument("--qids")
        p.add_argument(
            "--bank", required=True, help="read bank jsonl, one per benchmark"
        )
        p.add_argument("--env-file")
    sub.choices["import"].add_argument("--run", required=True)
    sub.choices["import"].add_argument("--profile", required=True)
    sub.choices["import"].add_argument(
        "--source",
        required=True,
        help="tag so the same saved answers are imported once",
    )
    sub.choices["bank"].add_argument("--run", required=True)
    sub.choices["bank"].add_argument("--profile", required=True)
    sub.choices["bank"].add_argument("--k", type=int, default=5)
    sub.choices["bank"].add_argument(
        "--cap", type=float, default=3.0, help="grader spend cap in USD"
    )
    sub.choices["bank"].add_argument("--concurrency", type=int, default=16)
    for name in ("compare",):
        sub.choices[name].add_argument(
            "--base", nargs=2, required=True, metavar=("RUN", "PROFILE")
        )
        sub.choices[name].add_argument(
            "--cand", nargs=2, required=True, metavar=("RUN", "PROFILE")
        )
        sub.choices[name].add_argument("--k", type=int, default=5)
        sub.choices[name].add_argument("--cap", type=float, default=3.0)
        sub.choices[name].add_argument(
            "--no-read", action="store_true", help="use only reads already in the bank"
        )
        sub.choices[name].add_argument(
            "--out", help="write per-question rows and the summary as JSON"
        )
    sub.choices["aa"].add_argument("--run", required=True)
    sub.choices["aa"].add_argument("--profile", required=True)
    sub.choices["aa"].add_argument("--k", type=int, default=5)
    sub.choices["aa"].add_argument("--resamples", type=int, default=20)
    args = parser.parse_args()
    if args.env_file:
        ra.load_env(args.env_file)
    questions = load_questions(args.questions, args.qids)
    bank = Bank(args.bank, ra.single_grader() or "benchmark")
    if args.command == "import":
        print(
            f"imported {import_run(bank, args.run, args.profile, questions, args.source)} reads"
        )
    elif args.command == "bank":
        jobs = [
            (q, c)
            for q in questions
            if (c := search_ctx(args.run, args.profile, q["id"])) is not None
        ]
        spend = asyncio.run(fill(bank, jobs, args.k, args.concurrency, args.cap))
        print(json.dumps(spend))
    elif args.command == "compare":
        if not args.no_read:
            jobs = []
            for q in questions:
                cb, cc = (
                    search_ctx(*args.base, q["id"]),
                    search_ctx(*args.cand, q["id"]),
                )
                if cb is not None and cc is not None and ctx_hash(cb) != ctx_hash(cc):
                    jobs += [(q, cb), (q, cc)]
            print(json.dumps(asyncio.run(fill(bank, jobs, args.k, cap=args.cap))))
        rows = paired(bank, questions, args.base, args.cand, args.k)
        result = summarize(rows, f"{args.cand[1]} vs {args.base[1]}")
        print(json.dumps(result, indent=1))
        if args.out:
            Path(args.out).write_text(
                json.dumps({"summary": result, "rows": rows}, indent=1)
            )
    elif args.command == "aa":
        result = aa(bank, questions, args.run, args.profile, args.k, args.resamples)
        print(json.dumps({k: v for k, v in result.items() if k != "runs"}, indent=1))


if __name__ == "__main__":
    main()
