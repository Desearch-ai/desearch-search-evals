"""The Desearch web index router's /search: ranked pages with query-time highlights."""

import asyncio
import time

import aiohttp

RETRYABLE = {429, 500, 502, 503, 504}
MODES = {"standard"}


def validate_profile(profile):
    if profile.get("transport") != "desearch_router":
        raise ValueError(
            "The Desearch router adapter requires transport=desearch_router"
        )
    if profile.get("mode") not in MODES:
        raise ValueError(f"Unsupported Desearch router mode: {profile.get('mode')}")
    for name, default in (("max_results", 10), ("max_characters", 2000)):
        value = profile.get(name, default)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(f"{name} must be a positive integer")
    extra = profile.get("request", {})
    if not isinstance(extra, dict) or {"query", "k"} & set(extra):
        raise ValueError(
            "request must be an object of extra router fields, without query or k"
        )


def build_request(question, profile):
    return {
        **profile.get("request", {}),
        "query": question,
        "k": profile.get("max_results", 10),
        "contents": {"highlights": {"max_chars": profile.get("max_characters", 2000)}},
    }


def parse_results(raw, profile):
    limit = profile.get("max_characters", 2000)
    return [
        {
            "rank": index + 1,
            "url": row.get("url") or "",
            "title": row.get("title") or "",
            "text": "\n".join(row.get("highlights") or [])[:limit],
            "published": (row.get("published") or "")[:10] or None,
            "scores": row.get("scores"),
            "highlight_scores": row.get("highlight_scores"),
        }
        for index, row in enumerate(
            (raw.get("results") or [])[: profile.get("max_results", 10)]
        )
    ]


def failed_parts(raw):
    """Shards or lanes that did not answer; a partial index answer is not a result."""
    return {name: value for name, value in (raw.get("failed") or {}).items() if value}


async def search(session, question, profile, router_url):
    validate_profile(profile)
    request = build_request(question, profile)
    started = time.monotonic()
    result = {
        "query": question,
        "observed_query": question,
        "request": request,
        "results": [],
        "search_calls": 1,
        "cost_usd": 0.0,
    }
    try:
        for attempt in range(4):
            async with session.post(
                router_url.rstrip("/") + "/search",
                json=request,
                timeout=aiohttp.ClientTimeout(total=90),
            ) as response:
                raw = await response.json(content_type=None)
                status = response.status
            if status == 200 and isinstance(raw, dict) and failed_parts(raw):
                status = 503
            if status not in RETRYABLE or attempt == 3:
                break
            await asyncio.sleep(2 ** (attempt + 1))
        result["attempts"] = attempt + 1
        if status != 200 or not isinstance(raw, dict):
            failed = failed_parts(raw) if isinstance(raw, dict) else None
            result["error"] = f"HTTP_{status}: {str(failed or raw)[:300]}"
        else:
            result["results"] = parse_results(raw, profile)
            result["server_ms"] = raw.get("ms")
            result["candidates"] = raw.get("candidates")
    except (aiohttp.ClientError, TimeoutError, ValueError) as error:
        result["error"] = f"{type(error).__name__}: {error}"
    result["elapsed_seconds"] = time.monotonic() - started
    return result
