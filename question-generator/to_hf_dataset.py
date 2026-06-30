"""Transform locally-generated questions into the locked public HF schema.

Reads output/questions/<label>.jsonl + output/golds/<label>.jsonl, recovers each
article's `published` (via the article cache, keyed by source_url), and emits
public rows {id, question, difficulty, start_date, end_date} bucketed by source
day into output/hf_dataset/questions/<YYYY-MM-DD>.jsonl. Golds stay local.

Usage:
  python to_hf_dataset.py --label 2026-06-24          # one generated batch
  python to_hf_dataset.py --label 2026-06-24 --only 2026-06-24  # restrict days
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from hf_schema import FMT, build_row, source_day, validate_row  # noqa: E402

ARTICLE_CACHE = HERE / "output" / ".cache" / "article"


def _cache_published(source_url: str) -> str | None:
    """Recover an article's published timestamp from the resumable fetch cache."""
    p = ARTICLE_CACHE / (hashlib.sha1(source_url.encode()).hexdigest()[:16] + ".json")
    if not p.exists():
        return None
    try:
        obj = json.loads(p.read_text())
    except Exception:
        return None
    return (obj or {}).get("published") or None


def _label_to_iso(label: str) -> str | None:
    """Fallback published when the article isn't cached: the date label at noon UTC."""
    try:
        d = datetime.strptime(label, "%Y-%m-%d").replace(hour=12, tzinfo=timezone.utc)
    except ValueError:
        return None
    return d.strftime(FMT)


def _normalize_z(iso: str) -> str:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc)
    return dt.strftime(FMT)


def _in_window(published_iso: str, start: str, end: str) -> bool:
    p = datetime.fromisoformat(published_iso.replace("Z", "+00:00")).astimezone(
        timezone.utc
    )
    s = datetime.strptime(start, FMT).replace(tzinfo=timezone.utc)
    e = datetime.strptime(end, FMT).replace(tzinfo=timezone.utc)
    return s <= p <= e


def load_batch(out_dir: Path, label: str) -> list[dict]:
    """Join public questions/<label> with golds/<label> to recover difficulty +
    source_url per question (golds never leave this function's output)."""
    qpath = out_dir / "questions" / f"{label}.jsonl"
    gpath = out_dir / "golds" / f"{label}.jsonl"
    if not qpath.exists() or not gpath.exists():
        raise SystemExit(f"missing batch files: {qpath} and/or {gpath}")

    diffs, questions = {}, {}
    for line in qpath.open():
        r = json.loads(line)
        diffs[r["id"]] = r["difficulty"]
        questions[r["id"]] = r["question"]

    rows = []
    for line in gpath.open():
        g = json.loads(line)
        rows.append(
            {
                "id": g["id"],
                "question": questions.get(g["id"], g["question"]),
                "difficulty": diffs.get(g["id"], "medium"),
                "source_url": g.get("source_url", ""),
                "label": g.get("date", label),
            }
        )
    return rows


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--label",
        required=True,
        help="date label of the generated batch (the --date used at generation)",
    )
    p.add_argument("--out", default=str(HERE / "output"))
    p.add_argument(
        "--only",
        nargs="*",
        default=None,
        help="restrict output to these source days (YYYY-MM-DD)",
    )
    args = p.parse_args()

    out_dir = Path(args.out)
    rows = load_batch(out_dir, args.label)
    hf_dir = out_dir / "hf_dataset" / "questions"
    hf_dir.mkdir(parents=True, exist_ok=True)

    only = set(args.only) if args.only else None
    by_day: dict[str, list[dict]] = {}
    no_pub = 0
    for r in rows:
        pub = _cache_published(r["source_url"]) or _label_to_iso(r["label"])
        if not pub:
            no_pub += 1
            continue
        pub = _normalize_z(pub)
        day = source_day(pub)
        if only is not None and day not in only:
            continue
        row = build_row(r["id"], r["question"], r["difficulty"], pub)
        validate_row(row)
        assert _in_window(pub, row["start_date"], row["end_date"]), (
            f"{pub} outside window"
        )
        by_day.setdefault(day, []).append(row)

    for day, day_rows in sorted(by_day.items()):
        path = hf_dir / f"{day}.jsonl"
        with path.open("w") as f:
            for row in day_rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"[hf] {path}  ({len(day_rows)} rows)")

    if no_pub:
        print(f"[warn] {no_pub} rows had no recoverable published date (skipped)")
    if not by_day:
        print("[hf] nothing written (no in-range rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
