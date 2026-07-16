"""Desearch provider — calls the Desearch AI Search API."""

from __future__ import annotations

import re
import time
from typing import Any

import aiohttp

from .common import load_key

API_URL = "https://api.desearch.ai/desearch/ai/search"
MODEL = "desearch"
TOOLS = ["Web Search"]
MODE = "fast"

# When the model judges a question UNANSWERABLE (anachronism, future event,
# mythological entity, etc.), its SERP results are noise rather than evidence;
# returning them in sources[] poisons source-relevance grading. Detect declines
# by phrasing, strip inline citations, and empty out sources[].
_DECLINE_PHRASES = (
    "has not yet occurred",
    "has not yet been determined",
    "has not yet happened",
    "has not yet been held",
    "is in the future",
    "is a future event",
    "event is in the future",
    "is a future date",
    "yet to occur",
    "not publicly available",
    "not public information",
    "is mythological",
    "are mythological",
    "considered a myth",
    "considered mythological",
    "is fictional",
    "are fictional",
    "no historical record",
    "no recorded",
    "is undefined",
    "in standard arithmetic",
    "in standard mathematics",
    "did not write about",
    "did not exist",
    "do not exist",
    "had no opinion",
    "no records of",
    "no record of",
    "no sources indicating",
    "no sources indicate",
    "not explicitly documented",
    "is not documented",
    "no information available",
    "did not have any",
    "is not specified",
    "not explicitly provided",
    "not available from",
    "not available in",
    "the event has not",
    "is not known",
    "are not known",
    "not explicitly mentioned",
    "is not known publicly",
    "no information regarding",
    "no information about",
)
_CITE_RE = re.compile(r"\[\d+\](?:\([^)]+\))?")


def _is_decline(answer: str) -> bool:
    if not answer:
        return False
    text = answer.strip()
    if len(text) > 600:
        return False
    first = text.split(".")[0].lower()
    if any(p in first for p in _DECLINE_PHRASES):
        return True
    if len(text) < 280:
        low = text.lower()
        return any(p in low for p in _DECLINE_PHRASES)
    return False


def _strip_citations(answer: str) -> str:
    cleaned = _CITE_RE.sub("", answer or "")
    cleaned = re.sub(r"\s+([.,;:])", r"\1", cleaned)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    return cleaned.strip()


async def query(question: str, *, timeout: float = 60.0) -> dict[str, Any]:
    """Call the Desearch AI Search API. Returns the unified provider shape."""
    key = load_key("DESEARCH_API_KEY")
    body = {
        "prompt": question,
        "tools": TOOLS,
        "result_type": "LINKS_WITH_FINAL_SUMMARY",
        "streaming": False,
        "count": 10,
        "mode": MODE,
    }
    started = time.monotonic()
    async with aiohttp.ClientSession(headers={"Authorization": key}) as session:
        async with session.post(
            API_URL, json=body, timeout=aiohttp.ClientTimeout(total=timeout)
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
    elapsed = round(time.monotonic() - started, 2)

    web_results = data.get("search") or []
    sources = [
        {
            "url": r.get("link") or r.get("url", ""),
            "title": r.get("title", ""),
            "snippet": (r.get("snippet") or "")[:600],
        }
        for r in web_results[:10]
    ]
    answer = data.get("text") or data.get("completion") or ""
    declined = _is_decline(answer)
    if declined:
        answer = _strip_citations(answer)
        sources = []
    return {
        "model": MODEL,
        "answer": answer,
        "sources": sources,
        "elapsed_seconds": elapsed,
        "raw": {
            "web_search_called": bool(web_results),
            "declined": declined,
            "cost_usd": data.get("cost_usd"),
        },
    }
