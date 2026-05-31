#!/usr/bin/env python3
"""Write the UI's offline fallback sample (a few questions) into public/data/.

Mirrors the HF layout so data.ts reads it with the same code path. The full run
lives on HuggingFace.

  python3 ui/refresh-data.py                 # newest results/<date>
  python3 ui/refresh-data.py --date 2026-05-31
  python3 ui/refresh-data.py --run-dir runs/2026-05-31
  python3 ui/refresh-data.py --sample 8
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
REPO = HERE.parent
RESULTS_DIR = REPO / "results"
DATA_OUT = HERE / "public" / "data"
DEFAULT_SAMPLE = 8


def resolve_date(date: str | None, run_dir: Path | None) -> str:
    if run_dir is not None:
        return run_dir.name
    if date:
        return date
    dates = sorted(p.stem for p in RESULTS_DIR.glob("*.jsonl"))
    if not dates:
        raise SystemExit(f"No results/*.jsonl found in {RESULTS_DIR}/")
    return dates[-1]


def main(date: str, sample: int) -> None:
    results_path = RESULTS_DIR / f"{date}.jsonl"
    if not results_path.exists():
        raise SystemExit(f"Missing {results_path}. Run scripts/weekly_run.py --date {date} first")

    rows = [json.loads(line) for line in results_path.read_text().split("\n") if line.strip()]
    keep_ids: list[str] = []
    for r in rows:
        qid = r.get("question_id")
        if qid and qid not in keep_ids:
            keep_ids.append(qid)
        if len(keep_ids) >= sample:
            break
    keep = set(keep_ids)
    sample_rows = [r for r in rows if r.get("question_id") in keep]

    sb_path = RESULTS_DIR / f"{date}.scoreboard.json"
    scoreboard = json.loads(sb_path.read_text()) if sb_path.exists() else None

    if DATA_OUT.exists():
        shutil.rmtree(DATA_OUT)
    (DATA_OUT / "results").mkdir(parents=True)
    (DATA_OUT / "scoreboards").mkdir(parents=True)

    (DATA_OUT / "results" / f"{date}.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in sample_rows) + "\n")

    if scoreboard is not None:
        (DATA_OUT / "scoreboards" / f"{date}.json").write_text(
            json.dumps(scoreboard, ensure_ascii=False))

    latest = {
        "date": date,
        "dates": [date],
        "questions": len(keep_ids),
        "rows": len(sample_rows),
        "providers": [r["provider"] for r in scoreboard["rows"]] if scoreboard
                     else sorted({r["provider"] for r in sample_rows}),
        "evaluators": (scoreboard or {}).get("evaluators_present", []),
        "weights": (scoreboard or {}).get("weights", {}),
        "files": {
            "results": f"results/{date}.jsonl",
            "questions": f"questions/{date}.jsonl",
            "scoreboard": f"scoreboards/{date}.json",
        },
        "isFallback": True,
        "schema_version": 1,
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    (DATA_OUT / "latest.json").write_text(json.dumps(latest, indent=2, ensure_ascii=False))

    print(f"Wrote fallback sample for {date}: {len(keep_ids)} questions, "
          f"{len(sample_rows)} rows → {DATA_OUT}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--date", default=None, help="YYYY-MM-DD; defaults to newest results/<date>")
    p.add_argument("--run-dir", type=Path, default=None,
                   help="A runs/<date>/ dir; the date is taken from its name")
    p.add_argument("--sample", type=int, default=DEFAULT_SAMPLE,
                   help=f"How many questions to keep in the fallback (default {DEFAULT_SAMPLE})")
    args = p.parse_args()
    main(resolve_date(args.date, args.run_dir), args.sample)
