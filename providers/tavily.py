import asyncio
import time

import aiohttp


def validate_profile(profile):
    if profile.get("transport", "tavily") != "tavily":
        raise ValueError("The Tavily search adapter requires Tavily transport")
    if profile.get("engine", "tavily") != "tavily":
        raise ValueError("The Tavily search adapter requires engine=tavily")
    if profile.get("mode") not in {"fast", "basic", "advanced"}:
        raise ValueError("Unsupported Tavily search mode")
    for name, default in (("max_results", 10), ("max_characters", 2000)):
        value = profile.get(name, default)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(f"{name} must be a positive integer")
    if any(
        profile.get(name)
        for name in (
            "allowed_domains",
            "excluded_domains",
            "start_date",
            "end_date",
            "date_filter",
        )
    ):
        raise ValueError("This Tavily adapter does not support domain or date filters")


RETRYABLE = {429, 500, 502, 503, 504}
ATTEMPTS = 5


async def search(session, question, profile, api_key):
    validate_profile(profile)
    if not isinstance(question, str) or not question.strip():
        raise ValueError("A nonempty question is required")
    body = {
        "query": question,
        "search_depth": profile["mode"],
        "max_results": profile.get("max_results", 10),
        "chunks_per_source": 3,
        "include_answer": False,
        "include_raw_content": False,
        "include_published_date": True,
        "auto_parameters": False,
        "topic": "general",
        "include_usage": True,
    }
    started = time.monotonic()
    for attempt in range(ATTEMPTS):
        async with session.post(
            "https://api.tavily.com/search",
            json=body,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=aiohttp.ClientTimeout(total=90),
        ) as response:
            raw = await response.json(content_type=None)
            status = response.status
        if status not in RETRYABLE:
            break
        if attempt < ATTEMPTS - 1:
            await asyncio.sleep(2 ** (attempt + 1))
    if status != 200:
        raise RuntimeError(f"Tavily returned {status}")
    results = [
        {
            "rank": index + 1,
            "url": row.get("url", ""),
            "title": row.get("title", ""),
            "text": row.get("content", "")[: profile.get("max_characters", 2000)],
            "published": row.get("published_date"),
        }
        for index, row in enumerate(raw.get("results", [])[: body["max_results"]])
    ]
    return {
        "query": question,
        "observed_query": raw.get("query"),
        "results": results,
        "raw": raw,
        "request": body,
        "search_calls": 1,
        "elapsed_seconds": time.monotonic() - started,
        "cost_usd": None,
        "usage_credits": raw.get("usage", {}).get("credits"),
        "error": None,
    }
