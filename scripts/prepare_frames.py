"""Freeze FRAMES from a pinned Hugging Face revision, with an optional seeded sample."""

import argparse
import ast
import csv
import hashlib
import json
import re
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

REPO = "google/frames-benchmark"
REVISION = "58d9fb6330f3ab1316d1eca12e5e8ef23dcc22ef"
SIZE = 824
START = re.compile(r"(?:[,;]\s*|\s+)(?=https?://|[a-z]{2,3}(?:\.m)?\.wikipedia\.org/)")


def source_url(revision=REVISION):
    return f"https://huggingface.co/datasets/{REPO}/resolve/{revision}/test.tsv"


def links(row):
    listed = ast.literal_eval(row.get("wiki_links") or "[]")
    columns = [
        value.strip()
        for name, value in row.items()
        if name and name.startswith("wikipedia_link_") and value and value.strip()
    ]
    urls = []
    for cell in [*listed, *columns]:
        for url in START.split(cell):
            url = (url.split() or [""])[0].split("#", 1)[0].strip(",;")
            if url and "://" not in url:
                url = "https://" + url
            if url and url not in urls:
                if "." not in urlsplit(url).netloc:
                    raise ValueError(f"Malformed reference URL: {url!r}")
                urls.append(url)
    return urls


def load(path, revision=REVISION):
    with Path(path).open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    records = []
    for position, row in enumerate(rows):
        question, answer = row.get("Prompt") or "", row.get("Answer") or ""
        if not question.strip() or not answer.strip():
            raise ValueError(f"Missing question or answer at row {position}")
        index = int(row.get("") or position)
        records.append(
            {
                "id": f"frames-{index}",
                "benchmark": "frames",
                "question": question,
                "answer": answer,
                "answer_aliases": [],
                "reference_urls": links(row),
                "reasoning_types": [
                    t.strip()
                    for t in (row.get("reasoning_types") or "").split("|")
                    if t.strip()
                ],
                "dataset_revision": f"{REPO}@{revision}",
            }
        )
    if len({r["id"] for r in records}) != len(records):
        raise ValueError("Row indices must be unique")
    return records


def sample(records, count, seed):
    ordered = sorted(
        records,
        key=lambda r: hashlib.sha256(f"{seed}\n{r['id']}".encode()).hexdigest(),
    )
    if count > len(ordered):
        raise ValueError(f"Sample of {count} exceeds {len(ordered)} questions")
    chosen = {r["id"] for r in ordered[:count]}
    return [r for r in records if r["id"] in chosen]


def jsonl(rows):
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--revision", default=REVISION)
    parser.add_argument("--sample", type=int, help="also write a seeded sample")
    parser.add_argument("--seed", default="42")
    args = parser.parse_args()
    out = Path(args.output)
    raw = out / "raw" / "test.tsv"
    if not raw.exists():
        raw.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(source_url(args.revision), raw)
    records = load(raw, args.revision)
    if len(records) != SIZE:
        raise SystemExit(f"Expected {SIZE} questions, got {len(records)}")
    files = {"questions.jsonl": jsonl(records)}
    if args.sample:
        files[f"sample{args.sample}.jsonl"] = jsonl(
            sample(records, args.sample, args.seed)
        )
    files["dataset.json"] = (
        json.dumps(
            {
                "benchmark": "frames",
                "source": source_url(args.revision),
                "sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
                "license": "apache-2.0",
                "seed": args.seed,
                "counts": {name: text.count("\n") for name, text in files.items()},
            },
            indent=2,
        )
        + "\n"
    )
    for name, text in files.items():
        target = out / name
        if target.exists() and target.read_text() != text:
            raise SystemExit(f"{target} changed; use a new output directory")
        target.write_text(text)
    print(files["dataset.json"])


if __name__ == "__main__":
    main()
