"""Run a question set against multiple providers in parallel, save per-provider outputs.

Each provider writes to <run_dir>/<provider>.json with the unified shape:

  [
    {
      "id": "...",
      "question": "...",
      "category": "...",
      "expected_answer": "..." (when known),
      "model": "tavily-advanced" | "exa-answer" | "sonar-pro" | ...,
      "answer": "...",
      "sources": [{"url", "title", "snippet"}, ...],
      "elapsed_seconds": float,
      "raw": {provider-specific fields incl. web_search_called},
      "error": "..." (only on failure)
    },
    ...
  ]

Usage:
  python3 providers/run_providers.py \
    --questions questions/2026-05-31.jsonl \
    --out-dir runs/2026-05-31/ \
    --providers tavily exa perplexity \
    --concurrency 5
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any

# Make `providers/` importable as a package when run from anywhere.
import sys

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from providers import desearch, exa, gpt5mini, perplexity, tavily  # noqa: E402

PROVIDERS = {
    "desearch": desearch.query,
    "gpt5mini": gpt5mini.query,
    "tavily": tavily.query,
    "exa": exa.query,
    "perplexity": perplexity.query,
}


async def run_provider(
    name: str, questions: list[dict], concurrency: int, out_path: Path
) -> None:
    """Run one provider over all questions; save incrementally."""
    fn = PROVIDERS[name]
    sem = asyncio.Semaphore(concurrency)
    results: list[dict[str, Any]] = []

    async def one(q: dict) -> dict[str, Any]:
        async with sem:
            t0 = time.monotonic()
            try:
                resp = await fn(q["question"])
                out = {**q, **resp}
            except Exception as e:
                out = {
                    **q,
                    "model": name,
                    "answer": "",
                    "sources": [],
                    "elapsed_seconds": round(time.monotonic() - t0, 2),
                    "error": f"{type(e).__name__}: {e}",
                }
            print(
                f"[{name}/{q['id']}] {out.get('elapsed_seconds', '?')}s · "
                f"{len(out.get('answer', '') or '')} chars · "
                f"{len(out.get('sources', []))} sources"
                + (f" · ERR {out['error']}" if out.get("error") else "")
            )
            return out

    started = time.monotonic()
    results = await asyncio.gather(*[one(q) for q in questions])
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(
        f"\n[{name}] wrote {out_path} ({len(results)} entries, "
        f"{time.monotonic() - started:.1f}s total wallclock)"
    )


def load_questions(path: Path) -> list[dict]:
    """Read questions from .jsonl (one object per line) or .json, assigning a
    per-file id when absent so providers and grades can be joined."""
    text = path.read_text()
    if path.suffix == ".jsonl":
        questions = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        raw = json.loads(text)
        questions = (
            raw["questions"] if isinstance(raw, dict) and "questions" in raw else raw
        )
    for i, q in enumerate(questions, 1):
        q.setdefault("id", f"q{i:04d}")
    return questions


async def main(
    questions_path: Path,
    out_dir: Path,
    provider_names: list[str],
    concurrency: int,
    limit: int | None = None,
) -> None:
    questions = load_questions(questions_path)
    if limit:
        questions = questions[:limit]

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "questions.json").write_text(
        json.dumps({"questions": questions}, indent=2, ensure_ascii=False)
    )

    # Run all providers concurrently — each provider sequences its own
    # requests via its semaphore.
    await asyncio.gather(
        *[
            run_provider(name, questions, concurrency, out_dir / f"{name}.json")
            for name in provider_names
        ]
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--questions", type=Path, required=True)
    p.add_argument("--out-dir", type=Path, required=True)
    p.add_argument(
        "--providers",
        nargs="+",
        default=list(PROVIDERS.keys()),
        choices=list(PROVIDERS.keys()),
    )
    p.add_argument("--concurrency", type=int, default=5)
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only run the first N questions (quick test)",
    )
    args = p.parse_args()
    asyncio.run(
        main(args.questions, args.out_dir, args.providers, args.concurrency, args.limit)
    )
