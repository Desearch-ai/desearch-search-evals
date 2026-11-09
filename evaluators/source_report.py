"""Question-level scores from graded results: did any top-k result state the verified answer."""

import argparse
import html
import json
import math
import random
from pathlib import Path

from utils import read_jsonl, write_json

KS = (1, 3, 5, 10)
BASES = ("page", "snippet")
BASELINE = "desearch-index"
BOOTSTRAP_RESAMPLES = 1000
BOOTSTRAP_SEED = 0
CI_METRICS = ("snippet_hit_at_10", "page_hit_at_10")
FRESHNESS_METRICS = ("page_hit_at_10", "snippet_hit_at_10", "gold_hit_at_10")


def question_scores(row: dict | None) -> dict:
    """Hit@k per evidence base and reciprocal rank of the first hit; failures score zero."""
    results = row["results"] if row and row.get("search_status") == "ok" else []
    scores = {}
    for basis in BASES:
        hits = [bool(r.get(basis)) and r[basis]["hit"] == 1 for r in results]
        for k in KS:
            scores[f"{basis}_hit_at_{k}"] = int(any(hits[:k]))
        first = next((index + 1 for index, hit in enumerate(hits) if hit), None)
        scores[f"{basis}_mrr"] = 1 / first if first else 0.0
    total = (row or {}).get("gold_urls") or 0
    if total:
        for k in KS:
            found = {r["gold"] for r in results[:k] if r.get("gold")}
            scores[f"gold_hit_at_{k}"] = int(bool(found))
            scores[f"gold_recall_at_{k}"] = len(found) / total
            scores[f"gold_all_at_{k}"] = int(len(found) == total)
    return scores


def _number(value) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def quantile(values: list[float], share: float) -> float | None:
    """Linearly interpolated quantile; None for no values."""
    ordered = sorted(values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * share
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def bootstrap_ci(values: list[float]) -> list[float] | None:
    """95% percentile interval of the mean, resampling questions with a fixed seed."""
    if not values:
        return None
    rng = random.Random(BOOTSTRAP_SEED)
    means = sorted(
        sum(rng.choices(values, k=len(values))) / len(values)
        for _ in range(BOOTSTRAP_RESAMPLES)
    )
    return [
        means[int(0.025 * BOOTSTRAP_RESAMPLES)],
        means[math.ceil(0.975 * BOOTSTRAP_RESAMPLES) - 1],
    ]


def _mean(values):
    return sum(values) / len(values) if values else None


def operations(rows: list[dict], results: list[dict]) -> dict:
    """Page access, speed, size and cost of one profile's searches."""
    pages = [r["page"] for r in results if r.get("page")]
    page_hits = [page for page in pages if page.get("hit") == 1]
    latencies = [
        r["elapsed_seconds"] for r in rows if _number(r.get("elapsed_seconds"))
    ]
    costs = [
        r["cost_usd"] for r in rows if _number(r.get("cost_usd")) and r["cost_usd"] >= 0
    ]
    chars = [r["returned_chars"] for r in results if _number(r.get("returned_chars"))]
    return {
        "fetch_rate": _mean([int(bool(page.get("fetched"))) for page in pages]),
        "page_hits_from_returned_text": _mean(
            [int(page.get("evidence_source") == "returned_text") for page in page_hits]
        ),
        "latency_p50_seconds": quantile(latencies, 0.5),
        "latency_p90_seconds": quantile(latencies, 0.9),
        "mean_returned_chars": _mean(chars),
        "cost_per_search_usd": _mean(costs),
        "searches_with_cost": len(costs),
    }


def paired(per_question: dict, baseline: str = BASELINE) -> dict | None:
    """Per-question @10 disagreements of every profile with the baseline."""
    if baseline not in per_question:
        return None
    out = {}
    for profile, scores in per_question.items():
        if profile == baseline:
            continue
        out[profile] = {}
        for basis in BASES:
            name = f"{basis}_hit_at_10"
            pairs = [
                (b.get(name, 0), s.get(name, 0))
                for b, s in zip(per_question[baseline], scores)
            ]
            out[profile][basis] = {
                "baseline_only": sum(b and not s for b, s in pairs),
                "profile_only": sum(s and not b for b, s in pairs),
                "both": sum(b and s for b, s in pairs),
                "neither": sum(not b and not s for b, s in pairs),
            }
    return {"baseline": baseline, "profiles": out}


def by_freshness(questions: list[dict], per_question: dict) -> dict | None:
    """Main @10 metrics per freshness class, when the questions carry one."""
    if not any(question.get("freshness") for question in questions):
        return None
    groups = {}
    for index, question in enumerate(questions):
        groups.setdefault(str(question.get("freshness") or "unspecified"), []).append(
            index
        )
    return {
        freshness: {
            "questions": len(indices),
            "profiles": {
                profile: {
                    name: sum(scores[i].get(name, 0) for i in indices) / len(indices)
                    if any(name in scores[i] for i in indices)
                    else None
                    for name in FRESHNESS_METRICS
                }
                for profile, scores in per_question.items()
            },
        }
        for freshness, indices in sorted(groups.items())
    }


def summarize(questions: list[dict], graded: dict, profile_ids: list[str]) -> dict:
    identifiers = [question["id"] for question in questions]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("Question identifiers must be unique")
    report = {
        "questions": len(questions),
        "judge": graded.get("judge"),
        "judge_calls": graded.get("judge_calls"),
        "recorded_cost_usd": graded.get("recorded_cost_usd"),
        "fetch_status_counts": graded.get("fetch_status_counts", {}),
        "profiles": {},
    }
    by_profile = {}
    for profile in profile_ids:
        by_id = {}
        for row in graded.get("profiles", {}).get(profile, []):
            if row["question_id"] not in identifiers or row["question_id"] in by_id:
                raise ValueError("Unknown or repeated question in profile rows")
            by_id[row["question_id"]] = row
        per_question = [question_scores(by_id.get(i)) for i in identifiers]
        by_profile[profile] = per_question
        names = dict.fromkeys(name for scores in per_question for name in scores)
        metrics = {
            name: sum(scores.get(name, 0) for scores in per_question)
            / len(per_question)
            for name in names
        }
        rows = list(by_id.values())
        results = [result for row in rows for result in row.get("results", [])]
        report["profiles"][profile] = {
            "metrics": metrics,
            "failed_searches": sum(row.get("search_status") != "ok" for row in rows),
            "missing_searches": len(identifiers) - len(rows),
            "empty_responses": sum(
                row.get("search_status") == "ok" and not row.get("results")
                for row in rows
            ),
            "results": len(results),
            "pages_fetched": sum(
                bool((r.get("page") or {}).get("fetched")) for r in results
            ),
            "pending_judgments": sum(
                (r.get(basis) or {}).get("status") == "judge_unavailable"
                for r in results
                for basis in BASES
            ),
            "judge_errors": sum(
                (r.get(basis) or {}).get("status") == "judge_error"
                for r in results
                for basis in BASES
            ),
            "mirrors": sum(bool(r.get("mirror")) for r in results),
            **operations(rows, results),
            "ci95": {
                name: bootstrap_ci([scores.get(name, 0) for scores in per_question])
                for name in CI_METRICS
            },
        }
    report["paired"] = paired(by_profile)
    report["by_freshness"] = by_freshness(questions, by_profile)
    return report


def _percent(value) -> str:
    return "-" if value is None else f"{100 * value:.1f}%"


def _md(value) -> str:
    return html.escape(str(value)).replace("|", "\\|").replace("\n", " ")


def _interval(values) -> str:
    return "-" if not values else f"{_percent(values[0])} – {_percent(values[1])}"


def _seconds(value) -> str:
    return "-" if value is None else f"{value:.2f} s"


def markdown(report: dict) -> str:
    ranks = " / ".join(f"@{k}" for k in KS)
    lines = [
        "# Search results",
        "",
        f"{report['questions']} questions.",
        "",
        f"| Profile | Page states answer {ranks} | Returned text states answer {ranks} | Gold URL {ranks} | Gold URL recall @10 | Page MRR@10 |",
        "| --- | --- | --- | --- | ---: | ---: |",
    ]
    for profile, result in report["profiles"].items():
        m = result["metrics"]
        page = " / ".join(_percent(m.get(f"page_hit_at_{k}")) for k in KS)
        snippet = " / ".join(_percent(m.get(f"snippet_hit_at_{k}")) for k in KS)
        gold = " / ".join(_percent(m.get(f"gold_hit_at_{k}")) for k in KS)
        lines.append(
            f"| {_md(profile)} | {page} | {snippet} | {gold} | "
            f"{_percent(m.get('gold_recall_at_10'))} | {m.get('page_mrr', 0):.3f} |"
        )
    lines += [
        "",
        (
            "A question is a hit when at least one of the top k results states the verified "
            "answer; any publisher counts and repeated coverage adds nothing. Page scores read "
            "the fetched page, falling back to the returned text when a page cannot be fetched. "
            "Returned-text scores read only what the API returned. Gold URL scores check whether the "
            "benchmark's own reference pages appear in the results, with no model involved; recall is "
            "the share of a question's reference pages found. Failed searches are misses."
        ),
        "",
        "## Operational outcomes",
        "",
        "| Profile | Failed | Missing | Empty | Results | Pages fetched | Judge errors | Benchmark mirrors |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for profile, r in report["profiles"].items():
        lines.append(
            f"| {_md(profile)} | {r['failed_searches']} | {r['missing_searches']} | "
            f"{r['empty_responses']} | {r['results']} | {r['pages_fetched']} | "
            f"{r['judge_errors']} | {r['mirrors']} |"
        )
    lines += [
        "",
        "## Page access, speed and cost",
        "",
        "| Profile | Page fetch rate | Page hits read from returned text | Search latency p50 / p90 | Mean returned chars per result | Cost per search |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for profile, r in report["profiles"].items():
        chars = r.get("mean_returned_chars")
        cost = r.get("cost_per_search_usd")
        lines.append(
            f"| {_md(profile)} | {_percent(r.get('fetch_rate'))} | "
            f"{_percent(r.get('page_hits_from_returned_text'))} | "
            f"{_seconds(r.get('latency_p50_seconds'))} / {_seconds(r.get('latency_p90_seconds'))} | "
            f"{'-' if chars is None else f'{chars:.0f}'} | "
            f"{'-' if cost is None else f'${cost:.4f}'} |"
        )
    lines += [
        "",
        "Page hits read from returned text are hits where the page could not be fetched and "
        "the judge read the provider's returned text instead. Cost per search averages the "
        "searches with a recorded cost.",
        "",
        "## 95% confidence intervals @10",
        "",
        f"Bootstrap over questions ({BOOTSTRAP_RESAMPLES} resamples, seed {BOOTSTRAP_SEED}).",
        "",
        "| Profile | Returned text states answer @10 | Page states answer @10 |",
        "| --- | ---: | ---: |",
    ]
    for profile, r in report["profiles"].items():
        ci = r.get("ci95") or {}
        lines.append(
            f"| {_md(profile)} | {_percent(r['metrics'].get('snippet_hit_at_10'))} "
            f"({_interval(ci.get('snippet_hit_at_10'))}) | "
            f"{_percent(r['metrics'].get('page_hit_at_10'))} "
            f"({_interval(ci.get('page_hit_at_10'))}) |"
        )
    comparison = report.get("paired")
    if comparison and comparison["profiles"]:
        baseline = _md(comparison["baseline"])
        lines += [
            "",
            f"## Paired against {baseline} @10",
            "",
            f"Questions where exactly one of the two is a hit: {baseline} only / profile only.",
            "",
            "| Profile | Page states answer | Returned text states answer |",
            "| --- | ---: | ---: |",
        ]
        for profile, counts in comparison["profiles"].items():
            cells = " | ".join(
                f"{counts[basis]['baseline_only']} / {counts[basis]['profile_only']}"
                for basis in BASES
            )
            lines.append(f"| {_md(profile)} | {cells} |")
    freshness = report.get("by_freshness")
    if freshness:
        lines += [
            "",
            "## By freshness @10",
            "",
            "| Freshness | Questions | Profile | Page states answer | Returned text states answer | Gold URL |",
            "| --- | ---: | --- | ---: | ---: | ---: |",
        ]
        for value, group in freshness.items():
            for profile, m in group["profiles"].items():
                lines.append(
                    f"| {_md(value)} | {group['questions']} | {_md(profile)} | "
                    f"{_percent(m['page_hit_at_10'])} | {_percent(m['snippet_hit_at_10'])} | "
                    f"{_percent(m['gold_hit_at_10'])} |"
                )
    pending = sum(r.get("pending_judgments", 0) for r in report["profiles"].values())
    if pending:
        lines += [
            "",
            f"**Answer scores are incomplete:** {pending} model judgments could not be made "
            "(the judge API was unavailable) and currently count as misses. Rerun grading to complete them.",
        ]
    calibration = report.get("calibration")
    if calibration:
        lines += [
            "",
            "## Judge accuracy",
            "",
            f"Against {calibration['labelled']} human-labelled cases: "
            f"{_percent(calibration['precision_of_hits'])} of counted hits really state the answer; "
            f"{_percent(calibration['share_of_rejections_that_answer'])} of rejected results do state it; "
            f"estimated recall of real hits {_percent(calibration['recall_of_hits'])}.",
        ]
    else:
        lines += [
            "",
            "Judge accuracy has not been calibrated against human labels for this run.",
        ]
    cost = report.get("recorded_cost_usd")
    if isinstance(cost, (int, float)):
        lines += [
            "",
            f"Judge: {report['judge']}, {report['judge_calls']} calls, ${cost:.2f}.",
        ]
    lines += [
        "",
        "These scores measure whether returned results carry the verified answer, not "
        "recall over every relevant page on the web.",
    ]
    return "\n".join(lines) + "\n"


def write_report(evaluation) -> dict:
    evaluation = Path(evaluation)
    questions = read_jsonl(evaluation.parent / "questions.jsonl")
    config = json.loads((evaluation.parent / "config.json").read_text())
    graded = json.loads((evaluation / "results.json").read_text())
    report = summarize(questions, graded, [p["id"] for p in config["profiles"]])
    calibration = evaluation / "calibration.json"
    if calibration.exists():
        report["calibration"] = json.loads(calibration.read_text())
    write_json(evaluation / "report.json", report)
    (evaluation / "report.md").write_text(markdown(report))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, help="Evaluation directory")
    args = parser.parse_args()
    print(markdown(write_report(args.run)))


if __name__ == "__main__":
    main()
