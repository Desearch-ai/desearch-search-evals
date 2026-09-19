"""Single-shot retrieval on a labelled corpus: how many evidence pages each question's search returns."""

import argparse
import asyncio
import json
import math
import os
from pathlib import Path
from urllib.parse import unquote, urlsplit

import aiohttp

from providers import desearch_index
from utils import load_env, read_jsonl, write_json

KS = (5, 10, 50)


def key(url: str) -> str:
    parts = urlsplit(url.strip())
    host = parts.netloc.lower().removeprefix("www.").removeprefix("m.")
    return host + (unquote(parts.path).rstrip("/") or "/")


def scores(found: list[str], evidence: set[str]) -> dict:
    ranked = [key(url) for url in found]
    hits = [url in evidence for url in ranked]
    result = {
        f"recall_at_{k}": len(set(ranked[:k]) & evidence) / len(evidence) for k in KS
    }
    ideal = sum(1 / math.log2(i + 2) for i in range(min(10, len(evidence))))
    result["ndcg_at_10"] = sum(
        1 / math.log2(i + 2) for i, hit in enumerate(hits[:10]) if hit
    ) / (ideal or 1)
    return result


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--mode", default="balanced")
    parser.add_argument("--out", required=True)
    parser.add_argument("--env-file", default=".env")
    args = parser.parse_args()

    load_env(args.env_file)
    key_value = os.environ["DESEARCH_INDEX_KEY"]
    profile = {
        "transport": "desearch_index",
        "mode": args.mode,
        "base_url": args.base_url,
        "max_results": max(KS),
    }
    questions = [q for q in read_jsonl(args.questions) if q.get("reference_urls")]
    gate = asyncio.Semaphore(8)

    async with aiohttp.ClientSession() as session:

        async def one(question):
            async with gate:
                found = await desearch_index.search(
                    session, question["question"], profile, key_value
                )
            evidence = {key(url) for url in question["reference_urls"]}
            return {
                "question_id": question["id"],
                **scores([r["url"] for r in found["results"]], evidence),
            }

        rows = await asyncio.gather(*(one(q) for q in questions))

    names = [f"recall_at_{k}" for k in KS] + ["ndcg_at_10"]
    summary = {
        "questions": len(rows),
        "mode": args.mode,
        **{name: sum(row[name] for row in rows) / len(rows) for name in names},
    }
    write_json(Path(args.out), {"summary": summary, "rows": rows})
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
