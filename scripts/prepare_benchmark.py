"""Freeze public factual-QA samples without sending reference data to search."""

import argparse
import ast
import csv
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

PROVENANCE = {
    "simpleqa": {
        "dataset_url": "https://openaipublic.blob.core.windows.net/simple-evals/simple_qa_test_set.csv",
        "description_url": "https://github.com/openai/simple-evals/blob/main/simpleqa_eval.py",
        "license": "MIT",
        "license_url": "https://github.com/openai/simple-evals/blob/main/LICENSE",
    },
    "simpleqa_verified": {
        "dataset_url": "https://huggingface.co/datasets/google/simpleqa-verified/resolve/main/simpleqa_verified.csv",
        "description_url": "https://huggingface.co/datasets/google/simpleqa-verified",
        "license": "MIT",
        "license_url": "https://huggingface.co/datasets/google/simpleqa-verified/blob/main/README.md",
    },
}


def sha256(value):
    return hashlib.sha256(value).hexdigest()


def question_partition(question):
    return int(hashlib.md5(question.encode()).hexdigest(), 16) % 2


def reference_urls(value):
    """Split only at URL starts, retaining commas inside individual URLs."""
    values = value if isinstance(value, list) else [value]
    urls = []
    for item in values:
        if not isinstance(item, str):
            raise TypeError("Reference URLs must be strings")
        for part in re.split(r"(?:[,;]\s*|\s+)(?=https?://)", item):
            url = part.strip().strip(" ,;'\"")
            if not url:
                continue
            while url.endswith(")") and url.count(")") > url.count("("):
                url = url[:-1]
            parsed = urlsplit(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError(f"Malformed reference URL: {url!r}")
            if url not in urls:
                urls.append(url)
    return urls


def load_benchmark(path, benchmark, original_simpleqa=None):
    """Preserve official question text, answer tolerances, and stable row IDs."""
    if benchmark not in PROVENANCE:
        raise ValueError("Unsupported benchmark")
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    originals = None
    if original_simpleqa:
        with Path(original_simpleqa).open(newline="", encoding="utf-8-sig") as handle:
            originals = list(csv.DictReader(handle))
    if benchmark == "simpleqa_verified" and originals is None:
        raise ValueError("Verified sampling requires the original SimpleQA CSV")

    records = []
    identifiers = set()
    for index, row in enumerate(rows):
        question, answer = row.get("problem"), row.get("answer")
        if not question or not question.strip() or not answer or not answer.strip():
            raise ValueError(f"Missing question or answer at row {index}")
        original_index = (
            int(row["original_index"]) if benchmark == "simpleqa_verified" else index
        )
        if original_index < 0 or original_index in identifiers:
            raise ValueError("Original question indices must be unique and nonnegative")
        identifiers.add(original_index)
        if originals is not None and original_index >= len(originals):
            raise ValueError("Original question index is outside the supplied CSV")
        metadata = ast.literal_eval(row["metadata"]) if benchmark == "simpleqa" else row
        if not isinstance(metadata, dict):
            raise TypeError("Benchmark metadata must be an object")
        variants = [question]
        if originals is not None:
            variants.append(originals[original_index]["problem"])
        held_out = all(question_partition(text) == 1 for text in variants)
        record = {
            "id": f"{'sqav' if benchmark == 'simpleqa_verified' else 'sqa'}-{original_index}",
            "benchmark": benchmark,
            "event_id": f"simpleqa-original-{original_index}",
            "question": question,
            "answer": answer,
            "facts": [{"id": "f1", "text": answer}],
            "reference_sources": [],
            "category": "public_factual_qa",
            "original_index": original_index,
            "topic": metadata.get("topic"),
            "answer_type": metadata.get("answer_type"),
            "reference_urls": reference_urls(metadata.get("urls", [])),
            "historical_partition": "held_out" if held_out else "development",
        }
        for name in ("multi_step", "requires_reasoning"):
            if name in row:
                if row[name].lower() not in {"true", "false"}:
                    raise ValueError(f"Invalid {name} metadata")
                record[name] = row[name].lower() == "true"
        records.append(record)
    return records


def select_samples(records, evaluation_count, calibration_count, seed):
    """Select before retrieval without filtering for index coverage or answers."""
    if evaluation_count < 1 or calibration_count < 0:
        raise ValueError("Evaluation count must be positive; calibration nonnegative")
    if len({row["id"] for row in records}) != len(records):
        raise ValueError("Record IDs must be unique")
    samples = {}
    for split, partition, count in (
        ("evaluation", "held_out", evaluation_count),
        ("calibration", "development", calibration_count),
    ):
        eligible = [r for r in records if r["historical_partition"] == partition]
        ordered = sorted(
            eligible,
            key=lambda row: sha256(f"{seed}\n{row['id']}".encode()),
        )
        if len(ordered) < count:
            raise ValueError(
                f"Insufficient {split} rows: need {count}, have {len(ordered)}"
            )
        samples[split] = ordered[:count]
    return samples


def json_text(value):
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def jsonl_text(rows):
    return "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows
    )


def write_sample(args):
    records = load_benchmark(args.source, args.benchmark, args.original_simpleqa)
    samples = select_samples(
        records, args.evaluation_count, args.calibration_count, args.seed
    )
    metadata = {
        "benchmark": args.benchmark,
        "provenance": PROVENANCE[args.benchmark],
        "seed": args.seed,
        "counts": {split: len(rows) for split, rows in samples.items()},
    }
    artifacts = {
        f"{split}/questions.jsonl": jsonl_text(rows) for split, rows in samples.items()
    }
    artifacts["dataset.json"] = json_text(metadata)
    output = Path(args.output)
    for name, text in artifacts.items():
        target = output / name
        if target.exists() and target.read_text() != text:
            raise ValueError(f"Sample changed; use a new output directory: {name}")
    for name, text in artifacts.items():
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    return metadata


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", choices=PROVENANCE, required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--original-simpleqa")
    parser.add_argument("--output", required=True)
    parser.add_argument("--evaluation-count", type=int, default=20)
    parser.add_argument("--calibration-count", type=int, default=10)
    parser.add_argument("--seed", default="42")
    metadata = write_sample(parser.parse_args())
    print(json_text(metadata["counts"]))


if __name__ == "__main__":
    main()
