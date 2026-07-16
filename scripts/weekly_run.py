#!/usr/bin/env python3
"""Run a date: providers, evaluators, aggregator, UI sample, then upload to HuggingFace.

Writes results/<date>.jsonl + results/<date>.scoreboard.json, which are pushed to
HuggingFace (see scripts/upload_to_hf.py) instead of committed to git.

  python3 scripts/weekly_run.py                  # today, all providers
  python3 scripts/weekly_run.py --date 2026-05-31
  python3 scripts/weekly_run.py --providers desearch perplexity
  python3 scripts/weekly_run.py --skip-run       # re-assemble runs/<date>/ only
  python3 scripts/weekly_run.py --no-upload      # skip the HuggingFace upload
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
QUESTIONS_DIR = REPO / "questions"
HF_DATASET_REPO = os.environ.get("HF_DATASET_REPO", "desearch/desearch-search-evals")
PROVIDERS = ["desearch", "gpt5mini", "tavily", "exa", "perplexity"]
EVALUATORS = ["source_relevance", "answer_quality", "groundedness"]


def _run(cmd: list[str]) -> None:
    print(f"\n$ {' '.join(cmd)}")
    subprocess.run(cmd, cwd=REPO, check=True)


def _hf_download_questions(run_date: str) -> Path | None:
    """Fetch questions/<date>.jsonl from HuggingFace (works without a token)."""
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        return None
    try:
        path = hf_hub_download(
            HF_DATASET_REPO,
            f"questions/{run_date}.jsonl",
            repo_type="dataset",
            token=os.environ.get("HF_TOKEN") or None,
        )
    except Exception:
        return None
    return Path(path)


def _hf_latest_date() -> str | None:
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        return None
    try:
        path = hf_hub_download(
            HF_DATASET_REPO,
            "latest.json",
            repo_type="dataset",
            token=os.environ.get("HF_TOKEN") or None,
        )
        return json.loads(Path(path).read_text()).get("date")
    except Exception:
        return None


def resolve_questions(run_date: str) -> Path:
    """Question sets are gitignored; look locally, then pull from HuggingFace."""
    dated = QUESTIONS_DIR / f"{run_date}.jsonl"
    if dated.exists():
        return dated

    hf = _hf_download_questions(run_date)
    if hf is not None:
        print(f"Using questions/{run_date}.jsonl from HuggingFace")
        return hf

    candidates = sorted(QUESTIONS_DIR.glob("*.jsonl"))
    if candidates:
        print(f"No questions/{run_date}.jsonl, using local {candidates[-1].name}")
        return candidates[-1]

    latest = _hf_latest_date()
    if latest:
        hf = _hf_download_questions(latest)
        if hf is not None:
            print(f"No local question sets, using HuggingFace latest ({latest})")
            return hf

    raise SystemExit(
        f"No questions/{run_date}.jsonl locally or on HuggingFace. Author "
        f"questions/{run_date}.jsonl, or pull a set from "
        f"https://huggingface.co/datasets/{HF_DATASET_REPO}"
    )


def run_pipeline(
    run_dir: Path,
    questions_path: Path,
    providers: list[str],
    concurrency: int,
    limit: int | None = None,
) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "providers/run_providers.py",
        "--questions",
        str(questions_path),
        "--out-dir",
        str(run_dir),
        "--providers",
        *providers,
        "--concurrency",
        str(concurrency),
    ]
    if limit:
        cmd += ["--limit", str(limit)]
    _run(cmd)
    for ev in EVALUATORS:
        _run([sys.executable, "-m", f"evaluators.{ev}", "--run-dir", str(run_dir)])
    _run([sys.executable, "-m", "evaluators.aggregator", "--run-dir", str(run_dir)])


def _grade_index(run_dir: Path, fname: str, field: str) -> dict[str, dict[str, object]]:
    """Map provider -> {question_id: value} for one grade file."""
    path = run_dir / fname
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    out: dict[str, dict[str, object]] = {}
    for provider, payload in (data.get("per_provider") or {}).items():
        out[provider] = {
            row["id"]: row.get(field) for row in payload.get("per_question", [])
        }
    return out


def assemble(run_dir: Path, run_date: str, providers: list[str]) -> None:
    relevance = _grade_index(run_dir, "grades_source_relevance.json", "mean_score")
    quality = _grade_index(run_dir, "grades_answer_quality.json", "score")
    verdict = _grade_index(run_dir, "grades_answer_quality.json", "verdict")
    grounded = _grade_index(run_dir, "grades_groundedness.json", "score")

    results_dir = REPO / "results"
    results_dir.mkdir(exist_ok=True)
    jsonl_path = results_dir / f"{run_date}.jsonl"

    rows = 0
    with jsonl_path.open("w") as f:
        for provider in providers:
            pfile = run_dir / f"{provider}.json"
            if not pfile.exists():
                continue
            for item in json.loads(pfile.read_text()):
                qid = item.get("id")
                f.write(
                    json.dumps(
                        {
                            "date": run_date,
                            "question_id": qid,
                            "difficulty": item.get("difficulty"),
                            "question": item.get("question"),
                            "provider": provider,
                            "model": item.get("model"),
                            "answer": item.get("answer", ""),
                            "sources": item.get("sources", []),
                            "elapsed_seconds": item.get("elapsed_seconds"),
                            "web_search_called": (item.get("raw") or {}).get(
                                "web_search_called"
                            ),
                            "source_relevance": relevance.get(provider, {}).get(qid),
                            "answer_quality": quality.get(provider, {}).get(qid),
                            "answer_quality_verdict": verdict.get(provider, {}).get(
                                qid
                            ),
                            "groundedness": grounded.get(provider, {}).get(qid),
                            "error": item.get("error"),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                rows += 1

    scoreboard_src = run_dir / "scoreboard.json"
    if scoreboard_src.exists():
        (results_dir / f"{run_date}.scoreboard.json").write_text(
            scoreboard_src.read_text()
        )

    print(f"\nWrote {jsonl_path} ({rows} rows)")


def refresh_ui(run_dir: Path) -> None:
    _run([sys.executable, "ui/refresh-data.py", "--run-dir", str(run_dir)])


def upload_hf(run_date: str) -> None:
    """Push questions + results + scoreboard to HuggingFace (no-ops without HF_TOKEN)."""
    _run([sys.executable, "scripts/upload_to_hf.py", "--date", run_date])


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--date", default=date.today().isoformat())
    p.add_argument("--providers", nargs="+", default=PROVIDERS, choices=PROVIDERS)
    p.add_argument("--concurrency", type=int, default=5)
    p.add_argument(
        "--skip-run",
        action="store_true",
        help="Assemble results from an existing runs/<date>/ without re-running",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only run the first N questions (quick test)",
    )
    p.add_argument(
        "--no-upload", action="store_true", help="Skip the HuggingFace upload step"
    )
    args = p.parse_args()

    run_dir = REPO / "runs" / args.date
    if not args.skip_run:
        run_pipeline(
            run_dir,
            resolve_questions(args.date),
            args.providers,
            args.concurrency,
            args.limit,
        )
    assemble(run_dir, args.date, args.providers)
    if run_dir.exists():
        refresh_ui(run_dir)
    if not args.no_upload:
        upload_hf(args.date)


if __name__ == "__main__":
    main()
