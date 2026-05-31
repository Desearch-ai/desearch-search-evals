"""Tavily provider — uses their answer endpoint (search + LLM synthesis).

Tavily's `search` API with `include_answer=true` returns both the search
results AND an LLM-generated answer string. Closest equivalent to what
GPT-5-mini's web_search tool produces.
"""

from __future__ import annotations

import time
from typing import Any

import aiohttp

from .common import load_key

API_URL = "https://api.tavily.com/search"


async def query(question: str, *, timeout: float = 60.0) -> dict[str, Any]:
    """Call Tavily search with answer synthesis enabled."""
    key = load_key("TAVILY_API_KEY")
    body = {
        "api_key": key,
        "query": question[:400],  # API limit
        "search_depth": "advanced",  # gives better source quality
        "include_answer": True,
        "include_raw_content": False,
        "max_results": 10,
    }
    started = time.monotonic()
    async with aiohttp.ClientSession() as session:
        async with session.post(
            API_URL, json=body, timeout=aiohttp.ClientTimeout(total=timeout)
        ) as resp:
            data = await resp.json()
    elapsed = round(time.monotonic() - started, 2)

    sources = [
        {
            "url": r.get("url", ""),
            "title": r.get("title", ""),
            "snippet": r.get("content", "")[:600],
        }
        for r in data.get("results", [])
    ]
    return {
        "model": "tavily-advanced",
        "answer": data.get("answer") or "",
        "sources": sources,
        "elapsed_seconds": elapsed,
        "raw": {
            "response_time": data.get("response_time"),
            "web_search_called": bool(sources),
        },
    }
