"""Evaluator #1 — source relevance.

For each cited URL in a provider's answer, fetch the page and ask the
judge LLM: "is this source actually relevant to the question?"
Yes / Maybe / No, scored 1.0 / 0.5 / 0.0.

Per-provider score: mean across all sources for all questions.
Skips questions where the provider returned no answer.

Usage:
  python3 -m evaluators.source_relevance --run-dir runs/2026-05-27
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

from .common import (
    cited_urls, discover_providers, fetch_pages, judge_chat,
    load_provider_outputs,
)

DEFAULT_PROMPT = """You are evaluating whether a web page is RELEVANT to answering a question.

Question: {question}

Cited source URL: {url}
Page title: {title}
Page excerpt (first {n} chars):
{excerpt}

Is this page relevant to answering the question?
- YES: the page is on-topic and could plausibly contain the answer or material that supports an answer.
- MAYBE: the page is loosely on-topic (mentions related entities) but unlikely to contain the answer.
- NO: the page is off-topic or unrelated.

Reply with one word: YES, MAYBE, or NO."""

PROMPT = DEFAULT_PROMPT  # mutated by --prompt-file CLI flag


def _parse(text: str) -> float:
    m = re.search(r"\b(YES|MAYBE|NO)\b", text.upper())
    if not m:
        return 0.0
    return {"YES": 1.0, "MAYBE": 0.5, "NO": 0.0}[m.group(1)]


async def grade_source(question: str, url: str, page: dict[str, str],
                       sem: asyncio.Semaphore) -> dict[str, Any]:
    # Use up to 3000 chars of page body (was 1500) so the judge sees enough
    # content to distinguish a "topic hub" from an "answer-bearing page" —
    # short hub pages used to slip through as "MAYBE" relevant.
    excerpt = page.get("text", "") or page.get("description", "") or ""
    excerpt = excerpt[:3000]
    if not excerpt and page.get("error"):
        return {"url": url, "score": 0.0, "verdict": "FETCH_FAILED",
                "error": page["error"]}
    prompt = PROMPT.format(
        question=question, url=url,
        title=page.get("title", "")[:200],
        n=len(excerpt), excerpt=excerpt,
    )
    async with sem:
        try:
            text = await judge_chat(prompt, max_tokens=8)
        except Exception as e:
            return {"url": url, "score": 0.0, "verdict": "JUDGE_ERROR",
                    "error": f"{type(e).__name__}: {e}"}
    score = _parse(text)
    return {"url": url, "score": score,
            "verdict": "YES" if score == 1.0 else ("MAYBE" if score == 0.5 else "NO")}


def _urls_for_question(item: dict, max_sources: int) -> list[str]:
    """Pick URLs to grade: the ones the model ACTUALLY CITED in its answer.
    Fall back to sources[] only when the answer has no inline citations.

    Why: judging a provider's full SERP punishes models that return 10
    sources and use 3, while rewarding models that return 3 and use 3.
    The question is "are the sources you used relevant?", not "is your
    raw search result list relevant?". For Tavily/Exa style providers
    that return many sources but cite few inline, this falls back to the
    top-N of sources[] — same as before, just more honest for models
    that cite specifically.
    """
    answer = item.get("answer", "") or ""
    cited = cited_urls(answer)
    if cited:
        return cited[:max_sources]
    return [s.get("url", "") for s in (item.get("sources") or [])
            if s.get("url")][:max_sources]


async def grade_provider(name: str, items: list[dict],
                         judge_concurrency: int, fetch_concurrency: int,
                         max_sources_per_q: int) -> dict[str, Any]:
    # Gather every URL we need to fetch first — pages are cached so even if
    # different providers cite the same URL we only fetch once.
    all_urls: set[str] = set()
    for it in items:
        for u in _urls_for_question(it, max_sources_per_q):
            if u:
                all_urls.add(u)
    print(f"[{name}] fetching {len(all_urls)} unique pages…")
    pages = await fetch_pages(all_urls, concurrency=fetch_concurrency)

    sem = asyncio.Semaphore(judge_concurrency)
    per_question: list[dict[str, Any]] = []

    async def go(item: dict) -> dict[str, Any]:
        urls = _urls_for_question(item, max_sources_per_q)
        if not urls:
            return {"id": item["id"], "n_sources": 0, "mean_score": None,
                    "per_source": []}
        results = await asyncio.gather(*[
            grade_source(item["question"], u, pages.get(u, {}), sem)
            for u in urls
        ])
        valid = [r["score"] for r in results if r["verdict"] != "FETCH_FAILED"]
        return {
            "id": item["id"],
            "category": item.get("category"),
            "n_sources": len(urls),
            "mean_score": (sum(valid) / len(valid)) if valid else None,
            "per_source": results,
        }

    per_question = await asyncio.gather(*[go(it) for it in items])

    scored = [q["mean_score"] for q in per_question if q["mean_score"] is not None]
    overall = sum(scored) / len(scored) if scored else None
    print(f"[{name}] source-relevance = "
          f"{overall:.3f}" if overall is not None else f"[{name}] no graded sources")
    return {
        "provider": name,
        "overall_score": overall,
        "graded_questions": len(scored),
        "per_question": per_question,
    }


async def main(run_dir: Path, judge_concurrency: int, fetch_concurrency: int,
               max_sources_per_q: int, out_name: str = "grades_source_relevance.json") -> None:
    providers = discover_providers(run_dir)
    if not providers:
        raise SystemExit(f"No provider outputs found in {run_dir}")

    started = time.monotonic()
    results = {}
    for p in providers:
        items = load_provider_outputs(run_dir / f"{p}.json")
        results[p] = await grade_provider(
            p, items, judge_concurrency, fetch_concurrency, max_sources_per_q,
        )

    out = {
        "evaluator": "source_relevance",
        "judge_model": "gpt-5.4-mini",
        "providers": {p: results[p]["overall_score"] for p in providers},
        "per_provider": results,
    }
    out_path = run_dir / out_name
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"\nWrote {out_path} (in {time.monotonic() - started:.1f}s)")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--run-dir", type=Path, required=True)
    p.add_argument("--judge-concurrency", type=int, default=20)
    p.add_argument("--fetch-concurrency", type=int, default=8)
    p.add_argument("--max-sources-per-q", type=int, default=5)
    p.add_argument("--prompt-file", type=Path, default=None,
                   help="Override PROMPT with the contents of this file")
    p.add_argument("--out-name", default="grades_source_relevance.json")
    args = p.parse_args()
    if args.prompt_file:
        PROMPT = args.prompt_file.read_text()
    asyncio.run(main(args.run_dir, args.judge_concurrency,
                     args.fetch_concurrency, args.max_sources_per_q,
                     args.out_name))
