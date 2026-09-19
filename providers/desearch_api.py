"""The public Desearch Search API, called the way a customer would."""

import asyncio
import time

import aiohttp

RETRYABLE = {429, 500, 502, 503, 504}
MODES = {"fast", "balanced"}


def validate_profile(profile):
    if profile.get("transport") != "desearch_api":
        raise ValueError("The Desearch API adapter requires transport=desearch_api")
    if profile.get("mode") not in MODES:
        raise ValueError(f"Unsupported Desearch search mode: {profile.get('mode')}")
    if not profile.get("base_url"):
        raise ValueError("Desearch API profiles need a base_url")


def build_request(question, profile):
    return {
        "query": question,
        "mode": profile["mode"],
        "count": profile.get("max_results", 10),
        "highlights": True,
        "page_text": False,
    }


def parse_results(raw, profile):
    limit = profile.get("max_characters", 2000)
    rows = raw.get("results") or []
    return [
        {
            "rank": index + 1,
            "url": row.get("url") or "",
            "title": row.get("title") or "",
            "published": row.get("published_date") or "",
            "text": " ".join(row.get("highlights") or [])[:limit],
        }
        for index, row in enumerate(rows)
    ]


async def search(session, question, profile, key):
    validate_profile(profile)
    body = build_request(question, profile)
    url = profile["base_url"].rstrip("/") + "/search"
    started = time.monotonic()

    for attempt in range(4):
        async with session.post(
            url,
            json=body,
            headers={"Authorization": key, "user-agent": "desearch-search-evals/1.0"},
            timeout=aiohttp.ClientTimeout(total=90),
        ) as response:
            if response.status in RETRYABLE and attempt < 3:
                await asyncio.sleep(2**attempt)
                continue
            raw = await response.json(content_type=None)
            if response.status != 200:
                raise RuntimeError(f"Desearch API returned {response.status}")
            break

    return {
        "query": question,
        "request": body,
        "results": parse_results(raw, profile),
        "search_calls": 1,
        "cost_usd": raw.get("cost_usd"),
        "raw": raw,
        "elapsed_seconds": time.monotonic() - started,
    }
