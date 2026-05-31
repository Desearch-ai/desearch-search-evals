"""GPT-5-mini via OpenAI Responses API with the web_search tool.

Returns the unified provider shape (model / answer / sources /
elapsed_seconds / raw). The `web_search` invocation flag is still
captured in `raw` as audit-only data — the evaluators no longer use
provider-reported flags for scoring.
"""

from __future__ import annotations

import time
from typing import Any

from openai import AsyncOpenAI

from .common import load_key

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=load_key("OPENAI_API_KEY"), timeout=120.0)
    return _client


def _count_search_calls(response: Any) -> int:
    n = 0
    for item in getattr(response, "output", []) or []:
        if getattr(item, "type", None) == "web_search_call":
            n += 1
    return n


def _extract_sources(response: Any) -> list[dict[str, str]]:
    sources: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in getattr(response, "output", []) or []:
        if getattr(item, "type", None) != "message":
            continue
        for block in getattr(item, "content", []) or []:
            text = getattr(block, "text", "") or ""
            for annotation in getattr(block, "annotations", []) or []:
                if getattr(annotation, "type", None) != "url_citation":
                    continue
                url = getattr(annotation, "url", None) or ""
                if not url or url in seen:
                    continue
                seen.add(url)
                start = getattr(annotation, "start_index", None)
                end = getattr(annotation, "end_index", None)
                snippet = text[start:end].strip() if (
                    isinstance(start, int) and isinstance(end, int)
                    and 0 <= start < end <= len(text)
                ) else ""
                sources.append({
                    "url": url,
                    "title": getattr(annotation, "title", "") or "",
                    "snippet": snippet,
                })
    return sources


async def query(question: str, *, model: str = "gpt-5-mini",
                timeout: float = 120.0) -> dict[str, Any]:
    """Call OpenAI Responses API with web_search tool."""
    client = _get_client()
    started = time.monotonic()
    try:
        response = await client.responses.create(
            model=model,
            tools=[{"type": "web_search"}],
            input=question,
        )
    except Exception as e:
        return {
            "model": model,
            "answer": "",
            "sources": [],
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "raw": {"error": f"{type(e).__name__}: {e}"},
        }

    n_calls = _count_search_calls(response)
    sources = _extract_sources(response)
    usage = getattr(response, "usage", None)
    return {
        "model": model,
        "answer": getattr(response, "output_text", "") or "",
        "sources": sources,
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "raw": {
            "input_tokens": getattr(usage, "input_tokens", None),
            "output_tokens": getattr(usage, "output_tokens", None),
            "web_search_call_count": n_calls,
            "web_search_called": n_calls > 0,
        },
    }
