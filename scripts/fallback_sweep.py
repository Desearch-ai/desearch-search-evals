"""Measure a query-time fallback trigger on saved searches: when our results look poor, add a second provider's results."""

import argparse
import asyncio
import json
import math
import os
import statistics
from collections import Counter
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
CONTROL = "{base}-top5"
CAUSES = (
    "hit",
    "excerpt",
    "coverage",
    "in index, below 10",
    "judged miss in top 10",
    "nobody answers",
    "not ours to hold",
    "unknown",
)
OURS_TO_FIX = {"excerpt", "in index, below 10", "judged miss in top 10"}


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


def score(row, name):
    value = (row.get("scores") or {}).get(name)
    return value if isinstance(value, (int, float)) else None


def signals(question, search):
    """Query-time signals, higher meaning more confident; router scores only when the search kept them."""
    rows = search.get("results") or []
    out = {"term_coverage": coverage(question, search)}
    if not rows or not any(r.get("scores") for r in rows):
        return out
    for name in ("rerank", "fused", "dense", "bm25"):
        values = [score(r, name) for r in rows[:10]]
        values = [v for v in values if v is not None]
        if values:
            out[f"top_{name}"] = max(values)
            out[f"mean3_{name}"] = statistics.fmean(sorted(values, reverse=True)[:3])
    spans = [max(r["highlight_scores"]) for r in rows[:3] if r.get("highlight_scores")]
    if spans:
        out["top_highlight"] = max(spans)
    found = (search.get("candidates") or {}).get("found")
    if isinstance(found, (int, float)):
        out["log_found"] = math.log1p(found)
    return out


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


def top_hit(row, basis):
    if not row or row.get("search_status") != "ok":
        return False
    return any(
        (r.get(basis) or {}).get("hit") == 1 and not r.get("mirror")
        for r in row["results"][:10]
    )


def cause(case):
    if case is None:
        return "unknown"
    if case["page_hit"]:
        return "hit" if case["snip_hit"] else "excerpt"
    if not case["answering"]:
        return "nobody answers"
    held = case.get("held") or {}
    ours = {k: v for k, v in held.items() if v != "not ours"}
    if held and not ours:
        return "not ours to hold"
    if not any(v is True for v in ours.values()):
        return "unknown" if any(v is None for v in ours.values()) else "coverage"
    if case.get("rank") is None:
        return "in index, below 10"
    return "judged miss in top 10" if case["rank"] <= 10 else "in index, below 10"


def expected(fit, question):
    return fit["intercept"] + fit["slope"] * math.log(len(question))


def fit_length(args):
    """Least squares of top_rerank on log question length, and the residual cut firing as often as the raw cut."""
    points = []
    for q in read_jsonl(args.questions):
        value = signals(q["question"], load(args.signals_run, args.base, q["id"])).get("top_rerank")
        if value is not None:
            points.append((math.log(len(q["question"])), value))
    xs, ys = zip(*points)
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    slope = sum((x - mx) * (y - my) for x, y in points) / sum((x - mx) ** 2 for x in xs)
    fit = {"intercept": my - slope * mx, "slope": slope, "fitted_on": args.questions, "n": len(points)}
    rate = sum(y < args.raw_cut for y in ys) / len(ys)
    residuals = sorted(y - fit["intercept"] - slope * x for x, y in points)
    fit |= {"raw_cut": args.raw_cut, "fire_rate": rate, "cut": residuals[min(len(residuals) - 1, round(rate * len(residuals)))]}
    Path(args.out).write_text(json.dumps(fit, indent=2) + "\n")
    print(json.dumps(fit))


def auc(rows, name):
    good = [r["signals"][name] for r in rows if r["text_hit"]]
    poor = [r["signals"][name] for r in rows if not r["text_hit"]]
    if not good or not poor:
        return None
    wins = sum((g > p) + 0.5 * (g == p) for g in good for p in poor)
    return wins / (len(good) * len(poor))


def gather(args):
    run = Path(args.run)
    questions = read_jsonl(args.questions)
    evaluation = json.loads(Path(args.base_eval).read_text())["profiles"][args.base]
    ours = {r["question_id"]: r for r in evaluation}
    grades_path = run / "answers" / "grades.jsonl"
    grades = {r["key"]: r["grade"] for r in read_jsonl(grades_path)}
    fb_path = run / "answers" / "fallback_grades.jsonl"
    fb = {r["key"]: r["grade"] for r in read_jsonl(fb_path)} if fb_path.exists() else {}
    theirs = {}
    if args.fallback_eval and Path(args.fallback_eval).exists():
        rows = json.loads(Path(args.fallback_eval).read_text())["profiles"]
        theirs = {r["question_id"]: r for r in rows.get(args.fallback, [])}
    cases = {}
    if args.misses and Path(args.misses).exists():
        cases = {c["qid"]: c for c in json.loads(Path(args.misses).read_text())}
    name = SPLIT.format(base=args.base, fallback=args.fallback)
    rows, overlap, same_top = [], [], 0
    fit = json.loads(Path(args.length_fit).read_text()) if args.length_fit else None
    for q in questions:
        saved = load(run, args.base, q["id"])
        live = load(args.signals_run, args.base, q["id"]) if args.signals_run else saved
        now = [url_key(r["url"]) for r in (live.get("results") or [])[:10]]
        then = [url_key(r["url"]) for r in (saved.get("results") or [])[:10]]
        overlap.append(len(set(now) & set(then)) / max(1, len(then)))
        same_top += bool(now and then and now[0] == then[0])
        extra = signals(q["question"], live)
        if fit and "top_rerank" in extra:
            extra["rerank_vs_length"] = extra["top_rerank"] - expected(fit, q["question"])
        extra["term_coverage"] = coverage(q["question"], saved)
        fallback = load(run, args.fallback, q["id"])
        rows.append(
            {
                "id": q["id"],
                "question": q,
                "signals": extra,
                "text_hit": top_hit(ours.get(q["id"]), "snippet"),
                "page_hit": top_hit(ours.get(q["id"]), "page"),
                "base": grades.get(f"{args.base}/{q['id']}") == "CORRECT",
                "merged": fb.get(f"{name}/{q['id']}"),
                "control": fb.get(f"{CONTROL.format(base=args.base)}/{q['id']}"),
                "fallback_text_hit": top_hit(theirs[q["id"]], "snippet")
                if q["id"] in theirs
                else None,
                "fallback_seconds": fallback.get("elapsed_seconds"),
                "fallback_ok": not fallback.get("error"),
                "cause": cause(cases.get(q["id"])) if cases else None,
            }
        )
    names = {k for r in rows for k in r["signals"]}
    for r in rows:
        for k in names:
            r["signals"].setdefault(k, -math.inf)
    agreement = (statistics.fmean(overlap) if overlap else 0.0, same_top)
    return rows, agreement


def fires(rows, name, rates, cuts=None):
    if not cuts:
        values = sorted(r["signals"][name] for r in rows)
        n = len(values)
        cuts = {values[min(n - 1, round(n * rate))] for rate in rates if n}
    return [(cut, [r for r in rows if r["signals"][name] < cut]) for cut in sorted(cuts)]


def pct(part, whole):
    return f"{part / whole:.0%}" if whole else "n/a"


def trigger_table(rows, names, rates, cuts=None):
    lines = [
        "| Signal | AUC | Fire below | Fallback rate | Precision (poor when fired) | Recall (poor caught) | False fallback (fires on our text hits) | Fires where our answer was right |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    poor = sum(not r["text_hit"] for r in rows)
    good = len(rows) - poor
    right = sum(r["base"] for r in rows)
    for name in names:
        area = auc(rows, name)
        for cut, fired in fires(rows, name, rates, cuts):
            hit = sum(not r["text_hit"] for r in fired)
            lines.append(
                f"| {name} | {area:.2f} | {cut:.3g} | {pct(len(fired), len(rows))} | "
                f"{pct(hit, len(fired))} | {pct(hit, poor)} | "
                f"{pct(len(fired) - hit, good)} | {pct(sum(r['base'] for r in fired), right)} |"
            )
    return lines


def fallback_table(rows, name, rates, price, cuts=None):
    n = len(rows)
    base = sum(r["base"] for r in rows)
    lines = [
        "| Fire below | Fallback rate | Ours alone | Ours + fallback | Gained / lost | Re-read of our top 5 alone: gained / lost | Fallback text@10 on fired (our text@10) | Fallback $ per 1k of our queries | Added latency per fallback, mean / p95 (per query, mean) |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for cut, fired in fires(rows, name, rates, cuts):
        if any(r["merged"] is None for r in fired):
            lines.append(
                f"| {cut:.3g} | {pct(len(fired), n)} | missing fallback answers |"
            )
            continue
        merged_right = [r["merged"] == "CORRECT" for r in fired]
        gained = sum(m and not r["base"] for m, r in zip(merged_right, fired))
        lost = sum(r["base"] and not m for m, r in zip(merged_right, fired))
        control = (
            "n/a"
            if any(r["control"] is None for r in fired)
            else f"+{sum(r['control'] == 'CORRECT' and not r['base'] for r in fired)} / -{sum(r['base'] and r['control'] != 'CORRECT' for r in fired)}"
        )
        judged = [r for r in fired if r["fallback_text_hit"] is not None]
        seconds = sorted(r["fallback_seconds"] or 0.0 for r in fired)
        mean = statistics.fmean(seconds) if seconds else 0.0
        p95 = (
            seconds[min(len(seconds) - 1, int(0.95 * len(seconds)))] if seconds else 0.0
        )
        lines.append(
            f"| {cut:.3g} | {pct(len(fired), n)} | {base / n:.1%} | {(base + gained - lost) / n:.1%} | "
            f"+{gained} / -{lost} | {control} | {pct(sum(r['fallback_text_hit'] for r in judged), len(judged))} "
            f"({pct(sum(r['text_hit'] for r in judged), len(judged))}) | "
            f"${len(fired) / n * 1000 * price:.2f} | {mean:.1f}s / {p95:.1f}s ({mean * len(fired) / n:.2f}s) |"
        )
    return lines


def cause_table(rows, name, rates, cuts=None):
    lines = [
        "| Fire below | Fired | "
        + " | ".join(CAUSES)
        + " | Gained / lost on coverage | Gained / lost on ours-to-fix |",
        "| ---: | ---: | " + " | ".join("---:" for _ in CAUSES) + " | ---: | ---: |",
    ]
    for cut, fired in fires(rows, name, rates, cuts):
        count = Counter(r["cause"] for r in fired)

        def swing(group):
            part = [r for r in fired if r["cause"] in group and r["merged"] is not None]
            gained = sum(r["merged"] == "CORRECT" and not r["base"] for r in part)
            lost = sum(r["base"] and r["merged"] != "CORRECT" for r in part)
            return f"+{gained} / -{lost}"

        lines.append(
            f"| {cut:.3g} | {len(fired)} | "
            + " | ".join(str(count.get(c, 0)) for c in CAUSES)
            + f" | {swing({'coverage'})} | {swing(OURS_TO_FIX)} |"
        )
    return lines


def report(args):
    rows, (overlap, same_top) = gather(args)
    n = len(rows)
    names = sorted({k for r in rows for k in r["signals"]}, key=lambda k: -(auc(rows, k) or 0))
    chosen = args.signal or names[0]
    poor = sum(not r["text_hit"] for r in rows)
    lines = [
        f"### {args.label}: fall back to {args.fallback} when {args.base}'s results look poor ({n} questions)",
        "",
        f"Poor means none of our top 10 returned texts states the answer (text@10 miss): {poor} of {n}."
        + (
            f" Router scores come from a rerun that shares {overlap:.0%} of the saved top 10 on average and the same first result on {same_top} of {n} questions."
            if args.signals_run
            else ""
        ),
        "",
        f"#### Trigger on {chosen}" + (" at fixed cuts" if args.cuts else ""),
        "",
        *trigger_table(rows, [chosen], args.rates, args.cuts),
        "",
        f"#### Fallback on {chosen}: ours + {args.fallback} (our top 5 + its top 5, URLs deduped) on fired questions",
        "",
        *fallback_table(rows, chosen, args.rates, args.price, args.cuts),
        "",
    ]
    if any(r["cause"] for r in rows):
        lines += [
            f"#### Why the fired questions missed (on {chosen})",
            "",
            *cause_table(rows, chosen, args.rates, args.cuts),
            "",
            "Coverage: the pages other providers answered from are not in our index. Excerpt: our page states the answer and our returned text does not. In index, below 10: the answering page is ours but ranked below 10 or past 100. The fix for excerpt and ranking misses is ours.",
            "",
        ]
    lines += [
        f"#### Every signal at matched fallback rates (top {args.top_signals} by AUC)",
        "",
        *trigger_table(rows, names[: args.top_signals], args.rates),
        "",
    ]
    lines.append(
        "Every signal is computed from the query and our results at query time; none reads the answer. AUC is how well the signal ranks our text hits above our misses."
    )
    text = "\n".join(lines) + "\n"
    if args.out:
        Path(args.out).write_text(text)
    print(text)


def flag(args):
    rows, _ = gather(args)
    chosen = args.signal or "term_coverage"
    cut, fired = fires(rows, chosen, args.rates, args.cuts)[-1]
    with open(args.out, "w") as handle:
        for r in fired:
            handle.write(json.dumps(r["question"]) + "\n")
    print(f"{len(fired)} of {len(rows)} flagged on {chosen} below {cut:.3g}")


async def answer(args):
    run_answers.load_env(args.env_file)
    run = Path(args.run)
    questions = read_jsonl(args.questions)
    name = CONTROL.format(base=args.base) if args.control else SPLIT.format(base=args.base, fallback=args.fallback)
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
                        load(run, args.base, q["id"]),
                        {} if args.control else load(run, args.fallback, q["id"]),
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["answer", "flag", "report", "fit"])
    parser.add_argument("--questions", required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--base", default="desearch-index")
    parser.add_argument("--fallback", default="octen")
    parser.add_argument(
        "--base-eval", help="results.json holding the base's judged searches"
    )
    parser.add_argument(
        "--fallback-eval", help="results.json holding the fallback's judged searches"
    )
    parser.add_argument(
        "--signals-run", help="run dir with a rerun of the base that kept router scores"
    )
    parser.add_argument(
        "--misses", help="misses.json with per-question coverage lookups"
    )
    parser.add_argument(
        "--signal",
        help="signal for the fallback and cause tables; default the best AUC",
    )
    parser.add_argument("--label", default="")
    parser.add_argument(
        "--price", type=float, default=0.001, help="fallback cost per search"
    )
    parser.add_argument(
        "--rates", type=float, nargs="+", default=[0.1, 0.2, 0.3, 0.4, 0.5]
    )
    parser.add_argument(
        "--cuts", type=float, nargs="+", help="fixed signal cuts in place of rate quantiles"
    )
    parser.add_argument("--top-signals", type=int, default=5)
    parser.add_argument("--length-fit", help="fit json from the fit command; adds rerank_vs_length")
    parser.add_argument("--raw-cut", type=float, default=4.0, help="fit: raw top_rerank cut whose fire rate the residual cut matches")
    parser.add_argument(
        "--control", action="store_true", help="answer from our top 5 alone, to size reader noise"
    )
    parser.add_argument("--today")
    parser.add_argument("--reader-tokens", type=int, default=6000)
    parser.add_argument("--concurrency", type=int, default=24)
    parser.add_argument("--out")
    parser.add_argument("--env-file", default=".env")
    args = parser.parse_args()
    if args.command == "answer":
        asyncio.run(answer(args))
    elif args.command == "fit":
        fit_length(args)
    elif args.command == "flag":
        flag(args)
    else:
        report(args)


if __name__ == "__main__":
    main()
