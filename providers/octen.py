"""Octen's Web Search API: ranked results with query-relevant highlights."""

import asyncio
import math
import time

import aiohttp

ENDPOINT = "https://api.octen.ai/search"
RETRYABLE = {429, 500, 502, 503, 504}
PRICE = 0.001
CHARS_PER_TOKEN = 4


def validate_profile(profile):
    if profile.get("transport") != "octen" or profile.get("engine", "octen") != "octen":
        raise ValueError("The Octen adapter requires transport=octen")
    for name, default in (("max_results", 10), ("max_characters", 2000)):
        value = profile.get(name, default)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(f"{name} must be a positive integer")
    if not 1 <= profile.get("max_results", 10) <= 100:
        raise ValueError("max_results is outside the provider's supported range")
    if any(
        profile.get(name)
        for name in ("allowed_domains", "excluded_domains", "start_date", "end_date")
    ):
        raise ValueError("Filters are not part of the benchmark profiles")


def build_request(question, profile):
    tokens = math.ceil(profile.get("max_characters", 2000) / CHARS_PER_TOKEN)
    return {
        "query": question,
        "count": profile.get("max_results", 10),
        "highlight": {"enable": True, "max_tokens": max(100, tokens)},
    }


def parse_results(raw, profile):
    limit = profile.get("max_characters", 2000)
    rows = (raw.get("data") or {}).get("results") or []
    return [
        {
            "rank": index + 1,
            "url": row.get("url") or "",
            "title": row.get("title") or "",
            "text": (row.get("highlight") or "")[:limit],
            "published": (row.get("time_published") or "")[:10] or None,
        }
        for index, row in enumerate(rows[: profile.get("max_results", 10)])
    ]


async def search(session, question, profile, api_key):
    validate_profile(profile)
    request = build_request(question, profile)
    started = time.monotonic()
    result = {
        "query": question,
        "observed_query": question,
        "request": request,
        "results": [],
        "search_calls": 1,
        "cost_usd": None,
    }
    try:
        for attempt in range(4):
            async with session.post(
                ENDPOINT,
                json=request,
                headers={"x-api-key": api_key},
                timeout=aiohttp.ClientTimeout(total=90),
            ) as response:
                raw = await response.json(content_type=None)
                status = response.status
            if status not in RETRYABLE or attempt == 3:
                break
            await asyncio.sleep(2 ** (attempt + 1))
        result["attempts"] = attempt + 1
        result["raw"] = raw
        if (
            status != 200
            or not isinstance(raw, dict)
            or raw.get("code") not in (0, None)
        ):
            code = raw.get("code") if isinstance(raw, dict) else None
            result["error"] = f"HTTP_{status}: code {code}: {str(raw)[:300]}"
        else:
            result["results"] = parse_results(raw, profile)
            result["server_ms"] = (raw.get("meta") or {}).get("latency")
            result["cost_usd"] = PRICE
    except (aiohttp.ClientError, TimeoutError, ValueError) as error:
        result["error"] = f"{type(error).__name__}: {error}"
    result["elapsed_seconds"] = time.monotonic() - started
    return result
