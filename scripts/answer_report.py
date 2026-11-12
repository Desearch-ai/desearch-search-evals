"""Accuracy, F1 and paired wins per profile from scripts.run_answers grades."""

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path

from scripts.run_answers import GRADERS, grader_model, grades_name, single_grader
from utils import read_jsonl

BASE = "desearch-index"


def f1(correct, attempted, n):
    overall = correct / n if n else 0.0
    given = correct / attempted if attempted else 0.0
    return 2 * overall * given / (overall + given) if overall + given else 0.0


def interval(hits, seed=7, rounds=1000):
    rng = random.Random(seed)
    n = len(hits)
    means = sorted(
        sum(hits[rng.randrange(n)] for _ in range(n)) / n for _ in range(rounds)
    )
    return means[int(0.025 * rounds)], means[int(0.975 * rounds) - 1]


def table(questions, grades, profiles, title):
    lines = [
        f"### {title} ({len(questions)} questions)",
        "",
        "| Profile | Accuracy (95% CI) | Not attempted | F1 | vs desearch-index (wins / losses) |",
        "| --- | --- | ---: | ---: | --- |",
    ]
    ids = [q["id"] for q in questions]
    for profile in profiles:
        rows = [grades.get(f"{profile}/{i}") for i in ids]
        if any(r is None for r in rows):
            lines.append(
                f"| {profile} | incomplete ({sum(r is not None for r in rows)}/{len(ids)}) | | | |"
            )
            continue
        hits = [r == "CORRECT" for r in rows]
        attempted = sum(r in ("CORRECT", "INCORRECT") for r in rows)
        low, high = interval(hits)
        paired = ""
        base = [grades.get(f"{BASE}/{i}") for i in ids]
        if profile != BASE and all(b is not None for b in base):
            wins = sum(h and b != "CORRECT" for h, b in zip(hits, base))
            losses = sum(not h and b == "CORRECT" for h, b in zip(hits, base))
            paired = f"{wins} / {losses}"
        lines.append(
            f"| {profile} | {sum(hits) / len(ids):.1%} ({low:.1%}–{high:.1%}) | {1 - attempted / len(ids):.1%} | {f1(sum(hits), attempted, len(ids)):.3f} | {paired} |"
        )
    return lines + [""]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--run", required=True)
    args = parser.parse_args()
    questions = list(read_jsonl(args.questions))
    profiles = [
        p["id"] for p in json.loads(Path(args.config).read_text())["profiles"]
    ] + ["no-search"]
    grades = {
        r["key"]: r["grade"]
        for r in read_jsonl(Path(args.run) / "answers" / grades_name())
    }
    lines = [
        "# Answer accuracy",
        "",
        "One search per question; the reader (Qwen3.6-27B, thinking on) sees each profile's top 10 returned texts, mirrors removed; graded with the benchmark's official prompt. `no-search` answers from model knowledge alone.",
        "",
    ]
    for benchmark in sorted({q.get("benchmark") for q in questions} & set(GRADERS)):
        grader = GRADERS[benchmark]
        lines += [
            f"Grader for {benchmark}: {grader_model(benchmark)} with `{grader['template']}`."
            + (f" {grader['note']}" if grader.get("note") and not single_grader() else ""),
            "",
        ]
    lines += table(questions, grades, profiles, "All")
    groups = defaultdict(list)
    for q in questions:
        if q.get("freshness"):
            groups[q["freshness"]].append(q)
    for name in sorted(groups):
        lines += table(groups[name], grades, profiles, f"Freshness: {name}")
    if any(q.get("seal_0") for q in questions):
        lines += table(
            [q for q in questions if q.get("seal_0")], grades, profiles, "Seal-0 subset"
        )
    kinds = defaultdict(list)
    for q in questions:
        for kind in q.get("reasoning_types") or []:
            kinds[kind].append(q)
    if kinds:
        lines.append("A FRAMES question can carry several reasoning types, so it counts in each.\n")
    for name in sorted(kinds, key=lambda k: -len(kinds[k])):
        lines += table(kinds[name], grades, profiles, f"Reasoning: {name}")
    out = Path(args.run) / "answers" / grades_name().replace("grades", "report").replace(".jsonl", ".md")
    out.write_text("\n".join(lines))
    print(out.read_text())


if __name__ == "__main__":
    main()
