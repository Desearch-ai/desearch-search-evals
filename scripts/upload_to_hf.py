#!/usr/bin/env python3
"""Push one date's questions, results, and scoreboard to the HuggingFace dataset.

  python3 scripts/upload_to_hf.py                # newest results/<date>.jsonl
  python3 scripts/upload_to_hf.py --date 2026-05-31
  python3 scripts/upload_to_hf.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from providers.common import load_env  # noqa: E402

RESULTS_DIR = REPO / "results"
DEFAULT_REPO = "desearch/desearch-search-evals"
DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})$")
PROVIDER_LABELS = {
    "desearch": "Desearch", "gpt5mini": "GPT-5-mini",
    "perplexity": "Perplexity sonar-pro", "tavily": "Tavily", "exa": "Exa",
}


def resolve_date(date: str | None) -> str:
    if date:
        if not DATE_RE.match(date):
            raise SystemExit(f"--date must be YYYY-MM-DD, got {date!r}")
        return date
    dates = sorted(p.stem for p in RESULTS_DIR.glob("*.jsonl"))
    if not dates:
        raise SystemExit(f"No results/*.jsonl found in {RESULTS_DIR}/")
    return dates[-1]


def read_results(date: str) -> list[dict]:
    path = RESULTS_DIR / f"{date}.jsonl"
    if not path.exists():
        raise SystemExit(f"Missing {path}. Run scripts/weekly_run.py --date {date} first")
    return [json.loads(line) for line in path.read_text().split("\n") if line.strip()]


def build_questions_jsonl(rows: list[dict]) -> bytes:
    """One line per distinct question, in question_id order."""
    seen: dict[str, dict] = {}
    for r in rows:
        qid = r.get("question_id")
        if qid and qid not in seen:
            seen[qid] = {
                "question_id": qid,
                "difficulty": r.get("difficulty"),
                "question": r.get("question"),
            }
    lines = [json.dumps(seen[q], ensure_ascii=False) for q in sorted(seen)]
    return ("\n".join(lines) + "\n").encode("utf-8")


def difficulty_counts(rows: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    seen: set[str] = set()
    for r in rows:
        qid = r.get("question_id")
        if qid and qid not in seen:
            seen.add(qid)
            counts[r.get("difficulty") or "unknown"] = counts.get(r.get("difficulty") or "unknown", 0) + 1
    return counts


def build_latest_json(date: str, dates: list[str], rows: list[dict],
                      scoreboard: dict | None, n_questions: int) -> bytes:
    providers = ([row["provider"] for row in scoreboard["rows"]] if scoreboard
                 else sorted({r["provider"] for r in rows}))
    payload = {
        "date": date,
        "dates": dates,
        "questions": n_questions,
        "rows": len(rows),
        "providers": providers,
        "evaluators": (scoreboard or {}).get("evaluators_present", []),
        "weights": (scoreboard or {}).get("weights", {}),
        "files": {
            "results": f"results/{date}.jsonl",
            "questions": f"questions/{date}.jsonl",
            "scoreboard": f"scoreboards/{date}.json",
        },
        "schema_version": 1,
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    return (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def build_card(repo_id: str, date: str, dates: list[str], rows: list[dict],
               scoreboard: dict | None, n_questions: int, diff: dict[str, int]) -> bytes:
    yaml = f"""---
license: cc-by-4.0
pretty_name: Desearch AI Search Benchmark
language:
  - en
tags:
  - ai-search
  - web-search
  - retrieval
  - rag
  - groundedness
  - benchmark
size_categories:
  - 1K<n<10K
task_categories:
  - question-answering
  - text-retrieval
configs:
  - config_name: results
    data_files:
      - split: latest
        path: results/{date}.jsonl
      - split: train
        path: results/*.jsonl
  - config_name: questions
    data_files:
      - split: latest
        path: questions/{date}.jsonl
      - split: train
        path: questions/*.jsonl
---
"""

    if scoreboard:
        w = scoreboard.get("weights", {})
        head = ("| Rank | Provider | Source relevance | Answer quality | Groundedness | Composite |\n"
                "|---|---|---|---|---|---|\n")
        body = ""
        for i, r in enumerate(scoreboard["rows"], 1):
            label = PROVIDER_LABELS.get(r["provider"], r["provider"])
            body += (f"| {i} | {label} | {r['source_relevance']:.3f} | {r['answer_quality']:.3f} "
                     f"| {r['groundedness']:.3f} | **{r['composite']:.3f}** |\n")
        weights_line = (f"Composite = {w.get('source_relevance', 0.40):.2f}*source_relevance "
                        f"+ {w.get('answer_quality', 0.30):.2f}*answer_quality "
                        f"+ {w.get('groundedness', 0.30):.2f}*groundedness.")
        leaderboard = f"## Latest leaderboard ({date})\n\n{head}{body}\n{weights_line}\n"
    else:
        leaderboard = ""

    diff_line = ", ".join(f"{k}: {v}" for k, v in sorted(diff.items()))
    dates_line = ", ".join(f"`{d}`" for d in dates)

    body = f"""# Desearch AI Search Benchmark

Open dataset for an **open-source benchmark comparing AI-search providers**
(Desearch, GPT-5-mini, Perplexity sonar-pro, Tavily, Exa) on the same questions,
graded behaviorally by an LLM judge (`gpt-5.4-mini`) rather than by string-matching.

- **Code & live leaderboard UI:** https://github.com/Desearch-ai/desearch-search-evals
- **Runs:** date-stamped, refreshed weekly and accumulating. Latest: **{date}**.
- **This run:** {n_questions} questions ({diff_line}), {len(rows)} graded rows across {len(set(r['provider'] for r in rows))} providers.
- **Available dates:** {dates_line}

{leaderboard}
## Files

| Path | What |
|---|---|
| `questions/<date>.jsonl` | The question set for a run: `question_id`, `difficulty`, `question`. |
| `results/<date>.jsonl` | One row per (question x provider) with the provider's answer, cited sources, and the three evaluator scores. |
| `scoreboards/<date>.json` | Per-provider composite for a run (what the leaderboard ranks on). |
| `latest.json` | Pointer to the newest `date` plus run metadata; the UI reads this first. |

Load the newest run with `datasets`:

```python
from datasets import load_dataset
ds = load_dataset("{repo_id}", "results", split="latest")
```

## results schema

| Field | Type | Notes |
|---|---|---|
| `date` | string | Run date (`YYYY-MM-DD`). |
| `question_id` | string | Stable within a run (`q0001`…). |
| `difficulty` | string | `easy` / `medium` / `hard`. |
| `question` | string | The prompt sent to every provider. |
| `provider` | string | `desearch`, `gpt5mini`, `perplexity`, `tavily`, `exa`. |
| `model` | string | Concrete model/endpoint the provider used. |
| `answer` | string | Provider answer (markdown with `[N](url)` citations). |
| `sources` | list | `{{url, title, snippet}}` cited by the provider. |
| `elapsed_seconds` | float | Provider wall-clock latency. |
| `web_search_called` | bool/null | Whether the provider actually searched, when it reports it. |
| `source_relevance` | float/null | Mean per-URL relevance (judge: YES/MAYBE/NO → 1/0.5/0). |
| `answer_quality` | float/null | 1.0 if RESPONSIVE or APPROPRIATE_DECLINE, else 0.0. |
| `answer_quality_verdict` | string/null | RESPONSIVE / APPROPRIATE_DECLINE / EVASIVE / WRONG_DECLINE / HALLUCINATED. |
| `groundedness` | float/null | Fraction of claims supported by a cited page. |
| `error` | string/null | Set only when the provider call failed. |

## Methodology (three judge-graded evaluators)

1. **Source relevance (40%)**: for each cited URL, the judge rules YES / MAYBE / NO (1 / 0.5 / 0). Catches on-topic-but-useless citations.
2. **Answer quality (30%)**: the judge classifies the answer (RESPONSIVE, APPROPRIATE_DECLINE, EVASIVE, WRONG_DECLINE, HALLUCINATED). Catches evasion and confident fabrication.
3. **Groundedness (30%)**: for each claim, the judge reads the cited page and rules SUPPORTED / CONTRADICTED / UNSUPPORTED. Catches hallucinated citations; proves a real search happened.

Questions are phrased durably ("current", "latest") so each stays valid while its answer
moves week to week, so there is no static answer key to memorize. Full methodology and the
interactive leaderboard live in the GitHub repo.

## License

Data released under **CC-BY-4.0**. The benchmark code is MIT (see the GitHub repo).
"""
    return (yaml + body).encode("utf-8")


def main() -> int:
    p = argparse.ArgumentParser(description="Upload a run to the HuggingFace dataset.")
    p.add_argument("--date", default=None, help="YYYY-MM-DD; defaults to newest results/<date>.jsonl")
    p.add_argument("--repo", default=os.environ.get("HF_DATASET_REPO", DEFAULT_REPO))
    p.add_argument("--dry-run", action="store_true", help="Build artifacts and report, but do not commit")
    args = p.parse_args()

    load_env()
    token = os.environ.get("HF_TOKEN", "").strip()
    if not token and not args.dry_run:
        print("HF_TOKEN not set, skipping HuggingFace upload (set it in .env to enable).")
        return 0

    date = resolve_date(args.date)
    rows = read_results(date)
    sb_path = RESULTS_DIR / f"{date}.scoreboard.json"
    scoreboard = json.loads(sb_path.read_text()) if sb_path.exists() else None

    questions_bytes = build_questions_jsonl(rows)
    results_bytes = (RESULTS_DIR / f"{date}.jsonl").read_bytes()
    n_questions = questions_bytes.decode().count("\n")
    diff = difficulty_counts(rows)

    from huggingface_hub import HfApi, CommitOperationAdd
    api = HfApi(token=token or None)

    existing: list[str] = []
    if token:
        try:
            existing = api.list_repo_files(args.repo, repo_type="dataset")
        except Exception as e:
            print(f"(could not list existing files: {e})")
    dates = sorted(
        {m.group(1) for f in existing
         if (m := re.match(r"results/(\d{4}-\d{2}-\d{2})\.jsonl$", f))} | {date}
    )

    latest_bytes = build_latest_json(date, dates, rows, scoreboard, n_questions)
    card_bytes = build_card(args.repo, date, dates, rows, scoreboard, n_questions, diff)

    ops = [
        CommitOperationAdd(f"questions/{date}.jsonl", questions_bytes),
        CommitOperationAdd(f"results/{date}.jsonl", results_bytes),
        CommitOperationAdd("latest.json", latest_bytes),
        CommitOperationAdd("README.md", card_bytes),
    ]
    if scoreboard is not None:
        ops.insert(2, CommitOperationAdd(f"scoreboards/{date}.json", sb_path.read_bytes()))

    print(f"Repo:       {args.repo} (dataset)")
    print(f"Date:       {date}  ({n_questions} questions, {len(rows)} rows, difficulty {diff})")
    print(f"All dates:  {dates}")
    print("Files:")
    for op in ops:
        size = len(op.path_or_fileobj) if isinstance(op.path_or_fileobj, (bytes, bytearray)) else "?"
        print(f"  + {op.path_in_repo:32s} {size} bytes")

    if args.dry_run:
        print("\n[dry-run] nothing committed.")
        return 0

    api.create_repo(args.repo, repo_type="dataset", exist_ok=True, private=False)
    info = api.create_commit(
        repo_id=args.repo,
        repo_type="dataset",
        operations=ops,
        commit_message=f"Add run {date} ({n_questions} questions, {len(rows)} rows)",
    )
    url = getattr(info, "commit_url", None) or f"https://huggingface.co/datasets/{args.repo}"
    print(f"\nCommitted: {url}")
    print(f"Browse:    https://huggingface.co/datasets/{args.repo}/tree/main")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
