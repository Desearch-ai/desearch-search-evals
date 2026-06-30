"""Retrofit 30k backfill questions with recovered source dates, split into dated JSONL."""

import argparse
import glob
import json
import os
from collections import Counter

from hf_schema import FORBIDDEN_KEYS, _parse, build_row, source_day, validate_row

CACHE_DIR = "output/.cache/article"
GOLDS = "output/golds/backfill.jsonl"
QUESTIONS = "output/questions/backfill.jsonl"
OUT_DIR = "output/hf_dataset/questions"


def load_url_to_published(cache_dir):
    url_to_pub = {}
    for fp in glob.iglob(os.path.join(cache_dir, "*.json")):
        try:
            with open(fp) as f:
                d = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        url, pub = d.get("url"), d.get("published")
        if url and pub:
            url_to_pub[url] = pub

    return url_to_pub


def load_id_to_url(golds_path):
    id_to_url = {}
    for line in open(golds_path):
        r = json.loads(line)
        id_to_url[r["id"]] = r.get("source_url")

    return id_to_url


# hf_schema.validate_row(row, published) raises naive-vs-aware; do the source-in-window check here.
def in_window(row, published):
    s = _parse(row["start_date"])
    e = _parse(row["end_date"])
    p = _parse(published)

    return s <= p <= e


def retrofit(url_to_pub, id_to_url, questions_path):
    rows_by_day = {}
    total = joined = dropped = 0
    for line in open(questions_path):
        total += 1
        q = json.loads(line)
        url = id_to_url.get(q["id"])
        published = url_to_pub.get(url) if url else None
        if not published:
            dropped += 1
            continue

        row = build_row(q["id"], q["question"], q["difficulty"], published)
        validate_row(row)
        assert in_window(row, published), (
            f"source {published} outside window for {q['id']}"
        )
        assert not (set(row) & FORBIDDEN_KEYS), f"forbidden key in row {q['id']}"

        rows_by_day.setdefault(source_day(published), []).append(row)
        joined += 1

    return rows_by_day, total, joined, dropped


def write_days(rows_by_day, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    for day, rows in rows_by_day.items():
        with open(os.path.join(out_dir, f"{day}.jsonl"), "w") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")

    return len(rows_by_day)


def print_summary(rows_by_day, total, joined, dropped, n_files):
    days = sorted(rows_by_day)
    difficulty = Counter(r["difficulty"] for rows in rows_by_day.values() for r in rows)
    samples = [r for rows in rows_by_day.values() for r in rows][:3]

    print("=== retrofit summary ===")
    print(f"total in:   {total}")
    print(f"joined:     {joined}")
    print(f"dropped:    {dropped} (no recoverable date)")
    print(f"files:      {n_files}")
    print(f"date range: {days[0]} .. {days[-1]}" if days else "date range: (none)")
    print(f"difficulty: {dict(difficulty)}")
    print("samples:")
    for s in samples:
        print(f"  {json.dumps(s)}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--push", action="store_true")
    args = parser.parse_args()

    url_to_pub = load_url_to_published(CACHE_DIR)
    id_to_url = load_id_to_url(GOLDS)
    rows_by_day, total, joined, dropped = retrofit(url_to_pub, id_to_url, QUESTIONS)
    n_files = write_days(rows_by_day, OUT_DIR)

    print_summary(rows_by_day, total, joined, dropped, n_files)

    if args.push:
        from push_to_hf import DEFAULT_REPO, push

        push(DEFAULT_REPO, dry_run=False)


if __name__ == "__main__":
    main()
