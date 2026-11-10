"""Evaluate one search profile on one benchmark (or a qid subset) against a saved baseline run, reusing its judge cache and unchanged answers."""

import argparse
import glob
import json
import os
import statistics
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.fallback_sweep import top_hit
from scripts.run_answers import NO_SEARCH, context

FREE = "(free)"


def holders(text):
    lines = (
        text.split("## Holder", 1)[1].splitlines()[1:] if "## Holder" in text else []
    )
    return [l for l in lines if l.strip() and l.strip() != FREE]


def acquire(path, line):
    text = Path(path).read_text()
    if "## Holder" not in text:
        raise SystemExit(f"{path} has no '## Holder' section")
    taken = holders(text)
    if taken:
        raise SystemExit("router is locked by: " + " / ".join(taken))
    head, _ = text.split("## Holder", 1)
    Path(path).write_text(f"{head}## Holder\n{line}\n")


def release(path, line):
    text = Path(path).read_text()
    if "## Holder" not in text:
        return
    head, _ = text.split("## Holder", 1)
    rest = [l for l in holders(text) if l != line]
    Path(path).write_text(
        f"{head}## Holder\n" + ("\n".join(rest) if rest else FREE) + "\n"
    )


def read_jsonl(path):
    return [json.loads(l) for l in Path(path).read_text().splitlines() if l.strip()]


def subset(questions, qids_file):
    if not qids_file:
        return questions
    wanted = {
        l.strip()
        for l in Path(qids_file).read_text().splitlines()
        if l.strip() and not l.startswith("#")
    }
    picked = [q for q in questions if q["id"] in wanted]
    missing = wanted - {q["id"] for q in picked}
    if missing:
        raise SystemExit(
            f"{len(missing)} qids are not in the questions file, e.g. {sorted(missing)[:3]}"
        )
    return picked


def search_rows(run, profile):
    out = {}
    for f in glob.glob(str(Path(run) / "searches" / profile / "*.json")):
        try:
            out[Path(f).stem] = json.loads(Path(f).read_text())
        except ValueError:
            pass
    return out


def reuse_answers(base, out, base_profile, profile, qids):
    """Copy the baseline's answer and grade for every question whose reader context is unchanged."""
    old, new = search_rows(base, base_profile), search_rows(out, profile)
    same = {
        q for q in qids if q in old and q in new and context(old[q]) == context(new[q])
    }
    kept = 0
    for name in ("answers.jsonl", "grades.jsonl"):
        src, dst = Path(base) / "answers" / name, Path(out) / "answers" / name
        if not src.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        have = {r["key"] for r in read_jsonl(dst)} if dst.exists() else set()
        rows = []
        for row in read_jsonl(src):
            prof, qid = row["key"].split("/", 1)
            if qid in qids and (
                prof == NO_SEARCH or (prof == base_profile and qid in same)
            ):
                target = NO_SEARCH if prof == NO_SEARCH else profile
                row = {**row, "key": f"{target}/{qid}", "profile": target}
                if row["key"] not in have:
                    rows.append(row)
        with dst.open("a") as f:
            f.writelines(json.dumps(r) + "\n" for r in rows)
        kept += len(rows)
    return same


def judged(run, profile):
    path = Path(run) / "evaluation" / "results.json"
    return (
        {
            r["question_id"]: r
            for r in json.loads(path.read_text())["profiles"].get(profile, [])
        }
        if path.exists()
        else {}
    )


def correct(run, profile):
    path = Path(run) / "answers" / "grades.jsonl"
    return (
        {
            r["key"].split("/", 1)[1]: r["grade"] == "CORRECT"
            for r in read_jsonl(path)
            if r["key"].startswith(profile + "/")
        }
        if path.exists()
        else {}
    )


def gold_hit(row):
    return (
        bool(row)
        and row.get("search_status") == "ok"
        and any(r.get("gold") for r in row["results"][:10])
    )


def summary(base, out, base_profile, profile, qids):
    jb, jn = judged(base, base_profile), judged(out, profile)
    gb, gn = correct(base, base_profile), correct(out, profile)
    searches = search_rows(out, profile)
    n = len(qids)

    def rate(values):
        return f"{100 * sum(values) / n:.1f}%" if n else "n/a"

    lines = [
        "| | Questions | Accuracy | Page@10 | Text@10 | Gold@10 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for label, j, g in (
        ("baseline " + base_profile, jb, gb),
        ("this run " + profile, jn, gn),
    ):
        lines.append(
            f"| {label} | {n} | {rate(g.get(q, False) for q in qids)} | {rate(top_hit(j.get(q), 'page') for q in qids)} "
            f"| {rate(top_hit(j.get(q), 'snippet') for q in qids)} | {rate(gold_hit(j.get(q)) for q in qids)} |"
        )
    gained = [q for q in qids if gn.get(q) and not gb.get(q)]
    lost = [q for q in qids if gb.get(q) and not gn.get(q)]
    tg = sum(
        top_hit(jn.get(q), "snippet") and not top_hit(jb.get(q), "snippet")
        for q in qids
    )
    tl = sum(
        top_hit(jb.get(q), "snippet") and not top_hit(jn.get(q), "snippet")
        for q in qids
    )
    elapsed = [
        r["elapsed_seconds"] for r in searches.values() if r.get("elapsed_seconds")
    ]
    errors = sum(1 for r in searches.values() if r.get("error"))
    ungraded = [q for q in qids if q not in gn]
    lines += [
        "",
        f"Answers vs baseline: +{len(gained)} / -{len(lost)}; text@10 +{tg} / -{tl}.",
        f"Search p50 {statistics.median(elapsed):.2f}s, errors {errors}."
        if elapsed
        else f"Search errors {errors}.",
    ]
    if ungraded:
        lines.append(
            f"Not graded (spend cap or reader failures): {len(ungraded)}, e.g. {ungraded[:5]}"
        )
    lines += [
        "",
        "Gained: " + (", ".join(gained) or "none"),
        "Lost: " + (", ".join(lost) or "none"),
    ]
    return "\n".join(lines)


def spend(out, since):
    judge = 0.0
    for f in glob.glob(str(Path(out) / "evaluation" / "calls" / "*.json")):
        if os.stat(f).st_mtime >= since:
            try:
                call = json.loads(Path(f).read_text())
            except ValueError:
                continue
            judge += sum(
                ((a.get("response") or {}).get("usage") or {}).get("cost") or 0
                for a in (call.get("trace") or {}).get("attempts") or []
            )
    return judge


def run_searches(args, n):
    started = datetime.now(timezone.utc)
    line = f"- {args.owner} | quick_eval {args.profile} on {Path(args.questions).parent.parent.name} ({n} questions, concurrency {args.concurrency}) | {started:%H:%M} UTC | ~{started + timedelta(seconds=3 * n / args.concurrency + 60):%H:%M} UTC"
    if args.stop and Path(args.stop).exists():
        raise SystemExit(f"stop file present: {Path(args.stop).read_text().strip()}")
    if args.lock:
        acquire(args.lock, line)
    try:
        command = [
            sys.executable,
            "-m",
            "providers.run_search",
            "--questions",
            str(args.out / "subset.jsonl"),
            "--config",
            str(args.out / "search-config.json"),
            "--run",
            str(args.out / "run"),
            "--profiles",
            args.profile,
            "--concurrency",
            str(args.concurrency),
        ]
        if args.env_file:
            command += ["--env-file", args.env_file]
        log = (args.out / "search.log").open("a")
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        begun = time.time()
        while process.poll() is None:
            time.sleep(5)
            reason = None
            if args.stop and Path(args.stop).exists():
                reason = "stop file: " + Path(args.stop).read_text().strip()
            else:
                fresh = [
                    r
                    for r in search_rows(args.out / "run", args.profile).values()
                    if r.get("completed_at")
                    and datetime.fromisoformat(r["completed_at"]).timestamp() >= begun
                ]
                bad = [
                    r["question_id"]
                    for r in fresh
                    if r.get("error") or (r.get("attempts") or 1) > 1
                ]
                recent = sorted(fresh, key=lambda r: r["completed_at"])[-40:]
                p50 = (
                    statistics.median(r["elapsed_seconds"] for r in recent)
                    if len(recent) >= 10
                    else 0
                )
                if bad:
                    reason = f"retried or failed: {bad[:5]}"
                elif p50 > args.max_p50:
                    reason = f"p50 {p50:.2f}s over {args.max_p50}s"
            if reason:
                process.terminate()
                process.wait()
                raise SystemExit(
                    f"searches stopped: {reason}; rerun the same command to resume"
                )
        if process.returncode:
            raise SystemExit(f"run_search failed, see {args.out / 'search.log'}")
    finally:
        if args.lock:
            release(args.lock, line)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base", required=True, help="baseline run dir, e.g. runs/<bench>/run-r3"
    )
    parser.add_argument(
        "--questions", required=True, help="the benchmark's questions jsonl"
    )
    parser.add_argument(
        "--qids", help="file with one question id per line; default all"
    )
    parser.add_argument(
        "--config", required=True, help="search config JSON holding the profile"
    )
    parser.add_argument(
        "--profile", required=True, help="profile id in --config to evaluate"
    )
    parser.add_argument(
        "--base-profile",
        default="desearch-index",
        help="baseline profile to compare against",
    )
    parser.add_argument(
        "--out", required=True, type=Path, help="new experiment dir; the run itself goes in <out>/run"
    )
    parser.add_argument(
        "--lock", help="router lock file; taken for the search phase and released after"
    )
    parser.add_argument("--stop", help="stop file; searches halt while it exists")
    parser.add_argument(
        "--owner", default="quick_eval", help="who holds the lock, written into it"
    )
    parser.add_argument("--concurrency", type=int, default=1, choices=[1, 2])
    parser.add_argument(
        "--max-p50",
        type=float,
        default=3.0,
        help="stop searches when the last 40 searches' p50 exceeds this",
    )
    parser.add_argument(
        "--cap", type=float, default=3.0, help="reader + grader spend cap in USD"
    )
    parser.add_argument(
        "--today",
        default="2026-11-09",
        help="date the reader is told; keep the baseline's",
    )
    parser.add_argument("--env-file")
    parser.add_argument(
        "--skip-search", action="store_true", help="reuse searches already in --out/run"
    )
    args = parser.parse_args()

    base = Path(args.base)
    questions = subset(read_jsonl(args.questions), args.qids)
    qids = [q["id"] for q in questions]
    profiles = [
        p
        for p in json.loads(Path(args.config).read_text())["profiles"]
        if p["id"] == args.profile
    ]
    if not profiles:
        raise SystemExit(f"profile {args.profile} is not in {args.config}")
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "subset.jsonl").write_text(
        "".join(json.dumps(q) + "\n" for q in questions)
    )
    (args.out / "search-config.json").write_text(
        json.dumps({"profiles": profiles}, indent=1)
    )
    run = args.out / "run"
    since = time.time()
    if not args.skip_search:
        run_searches(args, len(questions))
    print(f"searched {len(qids)} questions", flush=True)
    for name in ("page-cache", "evaluation/calls"):
        src, dst = base / name, run / name
        if src.exists() and not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["cp", "-cR", str(src), str(dst)], check=True)
    same = reuse_answers(base, run, args.base_profile, args.profile, set(qids))
    print(
        f"{len(same)} of {len(qids)} contexts unchanged; their answers are reused",
        flush=True,
    )
    env = ["--env-file", args.env_file] if args.env_file else []
    subprocess.run(
        [
            sys.executable,
            "-m",
            "evaluators.grade",
            "--run",
            str(run),
            "--out",
            str(run / "evaluation"),
            "--page-cache",
            str(run / "page-cache"),
            "--concurrency",
            "8",
            "--fetch-concurrency",
            "12",
            *env,
        ],
        check=True,
        stdout=(args.out / "grade.log").open("w"),
        stderr=subprocess.STDOUT,
    )
    for attempt in range(3):
        subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.run_answers",
                "--questions",
                str(args.out / "subset.jsonl"),
                "--config",
                str(run / "config.json"),
                "--run",
                str(run),
                "--today",
                args.today,
                "--cap",
                str(args.cap),
                "--reader-concurrency",
                "16" if attempt == 0 else "2",
                "--reader-tokens",
                "6000",
                *env,
            ],
            check=True,
            stdout=(args.out / "answers.log").open("a"),
            stderr=subprocess.STDOUT,
        )
        if set(qids) <= set(correct(run, args.profile)):
            break
        time.sleep(30)
    text = summary(base, run, args.base_profile, args.profile, qids)
    answers_log = (args.out / "answers.log").read_text().splitlines()
    text += f"\n\nJudge spend this run ${spend(run, since):.2f}; answers: {next((l for l in reversed(answers_log) if 'done:' in l), 'n/a')}"
    (args.out / "summary.md").write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
