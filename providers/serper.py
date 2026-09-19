"""Serper's Google SERP proxy: organic results with snippets, no page extraction."""

import asyncio
import time

import aiohttp

ENDPOINT = "https://google.serper.dev/search"
RETRYABLE = {429, 500, 502, 503, 504}
MODES = {"organic"}


def validate_profile(profile):
    if (
        profile.get("transport") != "serper"
        or profile.get("engine", "serper") != "serper"
    ):
        raise ValueError("The Serper adapter requires transport=serper")
    if profile.get("mode") not in MODES:
        raise ValueError(f"Unsupported Serper search mode: {profile.get('mode')}")


def build_request(question, profile):
    return {"q": question, "num": profile.get("max_results", 10)}


def parse_results(raw, profile):
    limit = profile.get("max_characters", 2000)
    rows = (raw or {}).get("organic") or []
    return [
        {
            "rank": row.get("position", index + 1),
            "url": row.get("link") or "",
            "title": row.get("title") or "",
            "published": row.get("date") or "",
            "text": (row.get("snippet") or "")[:limit],
        }
        for index, row in enumerate(rows)
    ]


async def search(session, question, profile, key):
    validate_profile(profile)
    body = build_request(question, profile)
    started = time.monotonic()

    for attempt in range(4):
        async with session.post(
            ENDPOINT,
            json=body,
            headers={"X-API-KEY": key},
            timeout=aiohttp.ClientTimeout(total=90),
        ) as response:
            if response.status in RETRYABLE and attempt < 3:
                await asyncio.sleep(2**attempt)
                continue
            raw = await response.json(content_type=None)
            if response.status != 200:
                raise RuntimeError(f"Serper returned {response.status}")
            break

    return {
        "query": question,
        "request": body,
        "results": parse_results(raw, profile),
        "search_calls": 1,
        "cost_usd": None,
        "raw": raw,
        "elapsed_seconds": time.monotonic() - started,
    }
