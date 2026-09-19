"""The Desearch index service called directly, for benchmarks that run on a fixed corpus."""

import asyncio
import time

import aiohttp

RETRYABLE = {429, 500, 502, 503, 504}
MODES = {"fast", "balanced"}
ATTEMPTS = 7


def validate_profile(profile):
    if profile.get("transport") != "desearch_index":
        raise ValueError("The Desearch index adapter requires transport=desearch_index")
    if profile.get("mode") not in MODES:
        raise ValueError(f"Unsupported Desearch index mode: {profile.get('mode')}")
    if not profile.get("base_url"):
        raise ValueError("Desearch index profiles need a base_url")


def parse_results(raw, profile):
    limit = profile.get("max_characters", 2000)
    return [
        {
            "rank": index + 1,
            "url": row.get("url") or "",
            "title": row.get("title") or "",
            "published": row.get("published_date") or "",
            "text": " ".join(row.get("highlights") or [])[:limit],
        }
        for index, row in enumerate(raw.get("results") or [])
    ]


async def _post(session, url, body, key):
    for attempt in range(ATTEMPTS):
        last = attempt == ATTEMPTS - 1
        try:
            async with session.post(
                url,
                json=body,
                headers={"Access-Key": key},
                timeout=aiohttp.ClientTimeout(total=60),
            ) as response:
                if response.status not in RETRYABLE or last:
                    return response.status, await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            if last:
                raise
        await asyncio.sleep(min(8, 2**attempt))


async def search(session, question, profile, key):
    validate_profile(profile)
    body = {
        "query": question,
        "mode": profile["mode"],
        "count": profile.get("max_results", 10),
        "highlights": True,
    }
    started = time.monotonic()
    status, raw = await _post(
        session, profile["base_url"].rstrip("/") + "/v1/search", body, key
    )
    if status != 200:
        raise RuntimeError(f"Desearch index returned {status}")
    return {
        "query": question,
        "request": body,
        "results": parse_results(raw, profile),
        "search_calls": 1,
        "cost_usd": 0.0,
        "confidence": raw.get("confidence"),
        "elapsed_seconds": time.monotonic() - started,
    }


async def document(session, url, profile, key):
    """Full text of an indexed page, or None when the corpus does not hold it."""
    status, raw = await _post(
        session, profile["base_url"].rstrip("/") + "/v1/document", {"url": url}, key
    )
    if status == 404:
        return None
    if status != 200:
        raise RuntimeError(f"Desearch index returned {status}")
    return raw.get("text") or ""
