"""Perplexity sonar-pro via OpenRouter (no $50 minimum required).

OpenRouter proxies Perplexity's sonar-pro through an OpenAI-compatible
endpoint with ~10% markup. Citations come through in the response as
either a top-level `citations` list, a `search_results` list, or message
annotations — we handle all three shapes.
"""

from __future__ import annotations

import time
from typing import Any

import aiohttp

from .common import load_key

API_URL = "https://openrouter.ai/api/v1/chat/completions"


async def query(question: str, *, model: str = "perplexity/sonar-pro",
                timeout: float = 60.0) -> dict[str, Any]:
    """Call sonar-pro via OpenRouter. Returns answer + cited URLs."""
    key = load_key("OPENROUTER_API_KEY")
    body = {
        "model": model,
        "messages": [{"role": "user", "content": question}],
    }
    started = time.monotonic()
    async with aiohttp.ClientSession() as session:
        async with session.post(
            API_URL,
            json=body,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                # OpenRouter encourages a referer header for analytics.
                "HTTP-Referer": "https://github.com/Desearch-ai/desearch-search-evals",
                "X-Title": "desearch-search-evals",
            },
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            data = await resp.json()
    elapsed = round(time.monotonic() - started, 2)

    msg = ((data.get("choices") or [{}])[0].get("message") or {})
    answer = msg.get("content", "") or ""

    # Gather citations from any of the three formats Perplexity uses:
    sources: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(url: str, title: str = "", snippet: str = "") -> None:
        if not url or url in seen:
            return
        seen.add(url)
        sources.append({"url": url, "title": title or "", "snippet": snippet or ""})

    # 1. search_results (richest format)
    for sr in (data.get("search_results") or []):
        add(sr.get("url", ""), sr.get("title", ""), sr.get("snippet", ""))

    # 2. message.annotations (OpenAI-compatible url_citation format)
    for ann in (msg.get("annotations") or []):
        if ann.get("type") == "url_citation":
            uc = ann.get("url_citation") or {}
            add(uc.get("url", ""), uc.get("title", ""), uc.get("content", ""))

    # 3. Top-level URL strings (older shape)
    for c in (data.get("citations") or []):
        url = c if isinstance(c, str) else (c.get("url") if isinstance(c, dict) else "")
        add(url or "")

    usage = data.get("usage") or {}
    return {
        "model": data.get("model") or model,
        "answer": answer,
        "sources": sources,
        "elapsed_seconds": elapsed,
        "raw": {
            "input_tokens": usage.get("prompt_tokens"),
            "output_tokens": usage.get("completion_tokens"),
            "web_search_called": bool(sources),
        },
    }
