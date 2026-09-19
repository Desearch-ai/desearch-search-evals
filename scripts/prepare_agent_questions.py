"""Freeze BrowseComp and DeepSearchQA into the question format the harness reads."""

import argparse
import base64
import csv
import hashlib
import io
import json
import random
from pathlib import Path

import requests

SOURCES = {
    "browsecomp": "https://openaipublic.blob.core.windows.net/simple-evals/browse_comp_test_set.csv",
    "deepsearchqa": "https://huggingface.co/datasets/google/deepsearchqa/resolve/main/DSQA-full.csv",
}


def decrypt(payload: str, password: str) -> str:
    """BrowseComp ships encrypted so the questions stay out of training data."""
    raw = base64.b64decode(payload)
    digest = hashlib.sha256(password.encode()).digest()
    key = digest * (len(raw) // len(digest)) + digest[: len(raw) % len(digest)]
    return bytes(a ^ b for a, b in zip(raw, key)).decode()


def browsecomp(rows):
    for index, row in enumerate(rows):
        canary = row["canary"]
        yield {
            "id": f"browsecomp-{index}",
            "benchmark": "browsecomp",
            "question": decrypt(row["problem"], canary),
            "answer": decrypt(row["answer"], canary),
        }


def deepsearchqa(rows):
    for index, row in enumerate(rows):
        yield {
            "id": f"dsqa-{index}",
            "benchmark": "deepsearchqa",
            "question": row["problem"],
            "answer": row["answer"],
            "answer_type": row.get("answer_type", ""),
            "category": row.get("problem_category", ""),
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", choices=sorted(SOURCES), required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--sample", type=int, help="Take this many questions at random")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    response = requests.get(SOURCES[args.benchmark], timeout=120)
    response.raise_for_status()
    rows = list(csv.DictReader(io.StringIO(response.text)))
    parse = browsecomp if args.benchmark == "browsecomp" else deepsearchqa
    questions = list(parse(rows))

    if args.sample:
        questions = random.Random(args.seed).sample(
            questions, min(args.sample, len(questions))
        )
        questions.sort(key=lambda q: q["id"])

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(q, ensure_ascii=False) + "\n" for q in questions))
    print(f"{len(questions)} {args.benchmark} questions -> {out}")


if __name__ == "__main__":
    main()
