"""Question-level scores from graded results: did any top-k result state the verified answer."""

import argparse
import html
import json
from pathlib import Path

from utils import read_jsonl, write_json

KS = (1, 3, 5, 10)
BASES = ("page", "snippet")


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
    for profile in profile_ids:
        by_id = {}
        for row in graded.get("profiles", {}).get(profile, []):
            if row["question_id"] not in identifiers or row["question_id"] in by_id:
                raise ValueError("Unknown or repeated question in profile rows")
            by_id[row["question_id"]] = row
        per_question = [question_scores(by_id.get(i)) for i in identifiers]
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
        }
    return report


def _percent(value) -> str:
    return "-" if value is None else f"{100 * value:.1f}%"


def _md(value) -> str:
    return html.escape(str(value)).replace("|", "\\|").replace("\n", " ")


def markdown(report: dict) -> str:
    lines = [
        "# Search results",
        "",
        f"{report['questions']} questions.",
        "",
        "| Profile | Page states answer @1 / @5 / @10 | Returned text states answer @1 / @5 / @10 | Gold URL @1 / @5 / @10 | Gold URL recall @10 | Page MRR@10 |",
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
