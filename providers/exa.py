"""Exa's own /search endpoint: ranked results with query-relevant highlights."""

import asyncio
import time

import aiohttp

ENDPOINT = "https://api.exa.ai/search"
RETRYABLE = {429, 500, 502, 503, 504}
MODES = {"fast", "auto"}


def validate_profile(profile):
    if profile.get("transport") != "exa" or profile.get("engine", "exa") != "exa":
        raise ValueError("The Exa adapter requires transport=exa")
    if profile.get("mode") not in MODES:
        raise ValueError(f"Unsupported Exa search type: {profile.get('mode')}")
    for name, default in (("max_results", 10), ("max_characters", 2000)):
        value = profile.get(name, default)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(f"{name} must be a positive integer")
    if any(
        profile.get(name)
        for name in ("allowed_domains", "excluded_domains", "start_date", "end_date")
    ):
        raise ValueError("Filters are not part of the benchmark profiles")


def build_request(question, profile):
    limit = profile.get("max_characters", 2000)
    return {
        "query": question,
        "type": profile["mode"],
        "numResults": profile.get("max_results", 10),
        "contents": {"highlights": {"query": question, "maxCharacters": limit}},
    }


def parse_results(raw, profile):
    limit = profile.get("max_characters", 2000)
    rows = raw.get("results") or []
    return [
        {
            "rank": index + 1,
            "url": row.get("url") or "",
            "title": row.get("title") or "",
            "text": "\n".join(row.get("highlights") or [])[:limit],
            "published": (row.get("publishedDate") or "")[:10] or None,
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
        result["cost_usd"] = (
            (raw.get("costDollars") or {}).get("total")
            if isinstance(raw, dict)
            else None
        )
        if status != 200 or not isinstance(raw, dict):
            result["error"] = f"HTTP_{status}: {str(raw)[:300]}"
        else:
            result["results"] = parse_results(raw, profile)
    except (aiohttp.ClientError, TimeoutError, ValueError) as error:
        result["error"] = f"{type(error).__name__}: {error}"
    result["elapsed_seconds"] = time.monotonic() - started
    return result
