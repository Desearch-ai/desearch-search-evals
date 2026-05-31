"""Exa provider — uses their /answer endpoint.

Exa's `/answer` endpoint returns an LLM-generated answer with citations.
This is the closest equivalent to GPT/Perplexity/Desearch's full-answer
products. (Exa also has a search-only endpoint, but for our comparison we
want each provider's best answer-producing capability.)
"""

from __future__ import annotations

import time
from typing import Any

import aiohttp

from .common import load_key

API_URL = "https://api.exa.ai/answer"


async def query(question: str, *, timeout: float = 60.0) -> dict[str, Any]:
    """Call Exa /answer for an LLM-synthesized response with citations."""
    key = load_key("EXA_API_KEY")
    body = {
        "query": question,
        "text": False,  # we collect title/url; full text bloats response
        "model": "exa",  # default Exa model — fast
    }
    started = time.monotonic()
    async with aiohttp.ClientSession() as session:
        async with session.post(
            API_URL,
            json=body,
            headers={"x-api-key": key, "Content-Type": "application/json"},
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            data = await resp.json()
    elapsed = round(time.monotonic() - started, 2)

    sources = []
    for r in data.get("citations", []) or []:
        sources.append({
            "url": r.get("url", ""),
            "title": r.get("title", ""),
            "snippet": (r.get("text") or r.get("snippet") or "")[:600],
        })
    return {
        "model": "exa-answer",
        "answer": data.get("answer") or "",
        "sources": sources,
        "elapsed_seconds": elapsed,
        "raw": {"web_search_called": bool(sources)},
    }
