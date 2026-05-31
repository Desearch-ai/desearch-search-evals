"""Evaluator #2 — groundedness.

For each factual claim in the provider's answer, ask the judge LLM:
"is this claim supported by the cited source's actual content?"

This is the evaluator that proves real-time search — a provider can't
hallucinate citations whose pages happen to support the claim without
the page actually being correct.

Per-question score: fraction of claims SUPPORTED.
Per-provider score: mean across questions.

Usage:
  python3 -m evaluators.groundedness --run-dir runs/2026-05-27
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
    cited_urls, discover_providers, extract_claims, fetch_pages, judge_chat,
    load_provider_outputs,
)

# Default judge prompt — v2_balanced, picked after the 2026-05-27 iteration
# loop (see iteration/README.md). Verbatim copy of
# iteration/prompts/groundedness_v2_balanced.md.
DEFAULT_PROMPT = """You check whether a factual CLAIM is SUPPORTED by the cited page's actual content. A "supported" claim is one a careful reader would conclude from what the page says.

Claim: {claim}

Cited source URL: {url}
Page title: {title}
Page content (first {n} chars):
{content}

Pick exactly ONE verdict.

SUPPORTED — the page content states the claim directly, OR the page's title plus excerpt taken together clearly establish it.
  - A page titled "DARA wins Eurovision 2026 for Bulgaria" SUPPORTS the claim "Dara from Bulgaria won Eurovision 2026 with Bangaranga" when the excerpt confirms the song name.
  - Numbers should agree on the order of magnitude and digit pattern. "Around $77,000" supports a claim of "$77,300". "$80,000" does not.
  - Names, places, dates must match (same person, same place, same month/year).
  - Hedging is fine when the page hedges similarly.

CONTRADICTED — the page makes a DIFFERENT specific claim about the SAME fact. Only use this verdict when you can quote the conflicting statement from the page.
  - Page says "X was born in 1923" but claim says "X was born in 1924" → CONTRADICTED.
  - Do NOT use CONTRADICTED just because the page doesn't say the claim — that's UNSUPPORTED.
  - Do NOT use CONTRADICTED for plausible-but-unverified claims.

UNSUPPORTED — the page does not address the claim. The page may be related to the topic but offers no specific evidence for or against this exact claim.
  - "The page is about Eurovision in general but doesn't mention the 2026 winner" → UNSUPPORTED.
  - Page is empty, error, or only contains navigation/JS shell → UNSUPPORTED.

Decision rules:
1. The page title is part of the page content for the purpose of judging. A clearly-named title is strong evidence.
2. If the excerpt is truncated mid-thought and the claim seems plausible, lean SUPPORTED if the title agrees; otherwise UNSUPPORTED.
3. When uncertain between SUPPORTED and UNSUPPORTED, pick UNSUPPORTED.
4. When uncertain between UNSUPPORTED and CONTRADICTED, ALWAYS pick UNSUPPORTED. CONTRADICTED requires an explicit conflicting statement from the page.
5. Do NOT use your own background knowledge — judge only from the page content provided.

Reply with ONLY one word: SUPPORTED, CONTRADICTED, or UNSUPPORTED."""

PROMPT = DEFAULT_PROMPT  # mutated by --prompt-file CLI flag


def _parse(text: str) -> str:
    m = re.search(r"\b(SUPPORTED|CONTRADICTED|UNSUPPORTED)\b", text.upper())
    return m.group(1) if m else "UNSUPPORTED"


def _score(verdict: str) -> float:
    return {"SUPPORTED": 1.0, "UNSUPPORTED": 0.0, "CONTRADICTED": 0.0}[verdict]


async def grade_claim(claim: str, url: str, page: dict[str, str],
                      sem: asyncio.Semaphore) -> dict[str, Any]:
    content = page.get("text", "") or page.get("description", "") or ""
    content = content[:3000]
    if not content and page.get("error"):
        return {"claim": claim[:120], "url": url, "verdict": "FETCH_FAILED",
                "score": 0.0, "error": page["error"]}
    prompt = PROMPT.format(
        claim=claim, url=url, title=page.get("title", "")[:200],
        n=len(content), content=content,
    )
    async with sem:
        try:
            text = await judge_chat(prompt, max_tokens=8)
        except Exception as e:
            return {"claim": claim[:120], "url": url, "verdict": "JUDGE_ERROR",
                    "score": 0.0, "error": f"{type(e).__name__}: {e}"}
    verdict = _parse(text)
    return {"claim": claim[:120], "url": url, "verdict": verdict,
            "score": _score(verdict)}


def _resolve_claim_urls(claim_urls: list[str], sources: list[dict],
                        max_check: int = 4) -> list[str]:
    """Which pages to check for support of this claim.
    Returns up to max_check URLs in priority order:
      1. URLs cited inline in the claim (the model's actual citations).
      2. First source[] if the claim has no inline citations.

    When a claim stacks 4+ citations (e.g. "Sinner is #1 [1, 3, 4, 5, 7]"),
    we check all of them. The claim gets credit if ANY one supports it
    (the honest reading of "this claim is grounded somewhere").
    """
    if claim_urls:
        return claim_urls[:max_check]
    if sources:
        u = sources[0].get("url")
        return [u] if u else []
    return []


async def grade_provider(name: str, items: list[dict],
                         judge_concurrency: int, fetch_concurrency: int,
                         max_claims: int) -> dict[str, Any]:
    # Pre-fetch every URL that might be cited: sources[] + inline answer URLs.
    urls: set[str] = set()
    for it in items:
        for s in it.get("sources") or []:
            if u := s.get("url"):
                urls.add(u)
        for u in cited_urls(it.get("answer", "") or ""):
            urls.add(u)
    print(f"[{name}] fetching {len(urls)} unique pages…")
    pages = await fetch_pages(urls, concurrency=fetch_concurrency)

    sem = asyncio.Semaphore(judge_concurrency)

    async def go(item: dict) -> dict[str, Any]:
        answer = item.get("answer", "") or ""
        sources = item.get("sources") or []
        claims = extract_claims(answer, max_claims=max_claims)
        if not claims or not sources:
            return {"id": item["id"], "category": item.get("category"),
                    "n_claims": 0, "score": None, "per_claim": []}

        async def one(claim_tuple):
            claim_text, claim_urls = claim_tuple
            urls_to_check = _resolve_claim_urls(claim_urls, sources)
            if not urls_to_check:
                return {"claim": claim_text[:120], "url": "", "verdict": "NO_URL",
                        "score": 0.0, "checked": 0}
            # Grade against EACH cited URL, take the best verdict.
            # SUPPORTED by any single page = claim is grounded.
            sub = await asyncio.gather(*[
                grade_claim(claim_text, u, pages.get(u, {}), sem)
                for u in urls_to_check
            ])
            best = max(sub, key=lambda r: r["score"])
            best_record = dict(best)
            best_record["checked"] = len(urls_to_check)
            best_record["alt_verdicts"] = [r["verdict"] for r in sub]
            return best_record

        results = await asyncio.gather(*[one(c) for c in claims])
        scored = [r["score"] for r in results
                  if r["verdict"] not in ("FETCH_FAILED", "NO_URL")]
        return {
            "id": item["id"], "category": item.get("category"),
            "n_claims": len(claims),
            "score": (sum(scored) / len(scored)) if scored else None,
            "per_claim": results,
        }

    per_question = await asyncio.gather(*[go(it) for it in items])
    scored = [q["score"] for q in per_question if q["score"] is not None]
    overall = sum(scored) / len(scored) if scored else None
    print(f"[{name}] groundedness = "
          f"{overall:.3f}" if overall is not None else f"[{name}] no graded claims")
    return {
        "provider": name, "overall_score": overall,
        "graded_questions": len(scored), "per_question": per_question,
    }


async def main(run_dir: Path, judge_concurrency: int, fetch_concurrency: int,
               max_claims: int, out_name: str = "grades_groundedness.json") -> None:
    providers = discover_providers(run_dir)
    if not providers:
        raise SystemExit(f"No provider outputs found in {run_dir}")

    started = time.monotonic()
    results = {}
    for p in providers:
        items = load_provider_outputs(run_dir / f"{p}.json")
        results[p] = await grade_provider(
            p, items, judge_concurrency, fetch_concurrency, max_claims,
        )

    out = {
        "evaluator": "groundedness",
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
    p.add_argument("--max-claims", type=int, default=6)
    p.add_argument("--prompt-file", type=Path, default=None,
                   help="Override PROMPT with the contents of this file")
    p.add_argument("--out-name", default="grades_groundedness.json")
    args = p.parse_args()
    if args.prompt_file:
        PROMPT = args.prompt_file.read_text()
    asyncio.run(main(args.run_dir, args.judge_concurrency,
                     args.fetch_concurrency, args.max_claims, args.out_name))
