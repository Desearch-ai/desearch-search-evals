"""Shared utilities for evaluators: judge model, page fetcher, contamination filter."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Iterable

import aiohttp

# Make sibling packages importable regardless of where this is run from.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from providers.common import load_env  # noqa: E402

load_env()

from openai import (  # noqa: E402
    AsyncOpenAI,
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
)

# Judge model — pinned for reproducible grading.
JUDGE_MODEL = "gpt-5.4-mini"
JUDGE_REASONING = "none"

_client: AsyncOpenAI | None = None


def get_judge() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(timeout=90.0)
    return _client


async def judge_chat(prompt: str, *, max_tokens: int | None = None) -> str:
    """One call to the pinned judge model, with retry/backoff on transient errors."""
    client = get_judge()
    kwargs: dict[str, Any] = {
        "model": JUDGE_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "reasoning_effort": JUDGE_REASONING,
    }
    if max_tokens is not None:
        kwargs["max_completion_tokens"] = max_tokens
    last_err: Exception | None = None
    for attempt in range(6):
        try:
            resp = await client.chat.completions.create(**kwargs)
            return (resp.choices[0].message.content or "").strip()
        except (
            RateLimitError,
            APITimeoutError,
            APIConnectionError,
            InternalServerError,
        ) as e:
            last_err = e
            await asyncio.sleep(min(2**attempt, 30))
    raise last_err if last_err else RuntimeError("judge_chat exhausted retries")


# ---------------------------------------------------------------------------
# Page fetching — used by source_relevance and groundedness evaluators.
# Cached on disk so repeated evaluator runs against the same provider outputs
# don't re-fetch the same URLs.
# ---------------------------------------------------------------------------

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_0) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# Optional rendered-fetch backend. With SCRAPINGDOG_API_KEY set, pages are fetched
# through scrapingdog with JS rendering + a 3s wait, so JS-injected values and
# bot-blocked pages become visible to the grader. Without the key, a plain static
# GET is used (cheap, no dependency) — the same code path for everyone.
SCRAPINGDOG_KEY = os.environ.get("SCRAPINGDOG_API_KEY") or ""
SCRAPINGDOG_URL = "https://api.scrapingdog.com/scrape"
SCRAPINGDOG_WAIT_MS = 3000
SCRAPINGDOG_CONCURRENCY = 10

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_META_DESC_RE = re.compile(
    r'<meta[^>]+name=[\'"]description[\'"][^>]+content=[\'"]([^\'"]+)[\'"]',
    re.IGNORECASE,
)
_OG_DESC_RE = re.compile(
    r'<meta[^>]+property=[\'"]og:description[\'"][^>]+content=[\'"]([^\'"]+)[\'"]',
    re.IGNORECASE,
)
_SCRIPT_STYLE_RE = re.compile(
    r"<(script|style)[^>]*>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")

PAGE_CACHE = _REPO_ROOT / "runs" / "_page_cache"


def _cache_path_for(url: str, backend: str = "static") -> Path:
    import hashlib

    h = hashlib.sha256(f"{backend}:{url}".encode("utf-8")).hexdigest()[:16]
    return PAGE_CACHE / f"{h}.json"


def _extract(html: str, body_chars: int) -> dict[str, str]:
    title = ""
    if m := _TITLE_RE.search(html):
        title = _WHITESPACE_RE.sub(" ", m.group(1)).strip()
    desc = ""
    if m := (_META_DESC_RE.search(html) or _OG_DESC_RE.search(html)):
        desc = m.group(1).strip()
    stripped = _SCRIPT_STYLE_RE.sub(" ", html)
    stripped = _TAG_RE.sub(" ", stripped)
    text = _WHITESPACE_RE.sub(" ", stripped).strip()[:body_chars]
    return {"title": title, "description": desc, "text": text, "error": ""}


async def fetch_page(
    session: aiohttp.ClientSession,
    url: str,
    *,
    timeout: float = 8.0,
    body_chars: int = 10000,
    use_cache: bool = True,
) -> dict[str, str]:
    """Fetch page, extract {title, description, text}. Cached to disk by (backend, URL).

    Routes through scrapingdog (JS render + 3s wait) when SCRAPINGDOG_API_KEY is
    set, else a plain static GET.
    """
    if not url:
        return {"title": "", "description": "", "text": "", "error": "empty url"}
    backend = "scrapingdog" if SCRAPINGDOG_KEY else "static"
    cache = _cache_path_for(url, backend)
    if use_cache and cache.exists():
        try:
            return json.loads(cache.read_text())
        except Exception:
            pass
    try:
        if SCRAPINGDOG_KEY:
            params = {
                "api_key": SCRAPINGDOG_KEY,
                "url": url,
                "dynamic": "true",
                "wait": str(SCRAPINGDOG_WAIT_MS),
            }
            async with session.get(
                SCRAPINGDOG_URL,
                params=params,
                timeout=aiohttp.ClientTimeout(total=max(timeout, 60.0)),
            ) as resp:
                html = await resp.text(errors="ignore")
                if resp.status != 200:
                    out = {
                        "title": "",
                        "description": "",
                        "text": "",
                        "error": f"scrapingdog {resp.status}: {html[:120]}",
                    }
                else:
                    out = _extract(html, body_chars)
        else:
            async with session.get(
                url,
                headers={"User-Agent": UA, "Accept": "text/html"},
                timeout=aiohttp.ClientTimeout(total=timeout),
                allow_redirects=True,
            ) as resp:
                ctype = resp.headers.get("content-type", "")
                if "html" not in ctype.lower():
                    out = {
                        "title": "",
                        "description": "",
                        "text": "",
                        "error": f"not html ({ctype})",
                    }
                else:
                    out = _extract(await resp.text(errors="ignore"), body_chars)
    except Exception as e:
        out = {
            "title": "",
            "description": "",
            "text": "",
            "error": f"{type(e).__name__}: {e}",
        }
    PAGE_CACHE.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(out, ensure_ascii=False))
    return out


async def fetch_pages(
    urls: Iterable[str], *, concurrency: int = 8, timeout: float = 8.0
) -> dict[str, dict[str, str]]:
    """Fetch many URLs concurrently. Returns {url: page}."""
    urls = list(dict.fromkeys(urls))  # de-dupe, preserve order
    if SCRAPINGDOG_KEY:
        concurrency = min(concurrency, SCRAPINGDOG_CONCURRENCY)
    sem = asyncio.Semaphore(concurrency)
    async with aiohttp.ClientSession() as session:

        async def one(u: str) -> tuple[str, dict[str, str]]:
            async with sem:
                return u, await fetch_page(session, u, timeout=timeout)

        return dict(await asyncio.gather(*[one(u) for u in urls]))


# ---------------------------------------------------------------------------
# Contamination filter — strip search hits that point at the dataset itself.
# Modeled on Perplexity's published evals.
# ---------------------------------------------------------------------------

CONTAMINATION_PATTERNS = {
    # SimpleQA artifacts
    "simpleqa",
    "simple_qa",
    "simple-qa",
    # FRAMES (Google)
    "frames-benchmark",
    "frames_benchmark",
    # BrowseComp (OpenAI)
    "browsecomp",
    "browse_comp",
    "browse-comp",
    # DeepSearchQA (DeepMind)
    "deepsearchqa",
    "deep_search_qa",
    "deep-search-qa",
    "dsqa",
    # SEAL
    "seal-0",
    "seal_0",
    "sealhard",
    "seal-hard",
    "seal_hard",
    # HuggingFace dataset cards (very common contamination vector)
    "huggingface.co/datasets",
}


def is_contaminated(url: str, title: str = "", snippet: str = "") -> bool:
    """Does this hit point at a dataset page rather than primary source?"""
    blob = f"{url} {title} {snippet}".lower()
    return any(p in blob for p in CONTAMINATION_PATTERNS)


def strip_contamination(sources: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split sources into (kept, contaminated)."""
    kept: list[dict] = []
    contaminated: list[dict] = []
    for s in sources or []:
        if is_contaminated(s.get("url", ""), s.get("title", ""), s.get("snippet", "")):
            contaminated.append(s)
        else:
            kept.append(s)
    return kept, contaminated


# ---------------------------------------------------------------------------
# Claim extraction — used by groundedness evaluator.
# Splits an answer on sentence boundaries (very simple — judges are robust).
# Returns (claim_text, [urls_cited_in_claim]) so the evaluator knows which
# page to check against — NOT just the first source in the response.
# ---------------------------------------------------------------------------

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")
_INLINE_LINK_RE = re.compile(r"\[[^\]]*\]\((https?://[^)\s]+)\)")
_BARE_URL_RE = re.compile(r"https?://[^\s)>\]]+")


def _urls_in(text: str) -> list[str]:
    found = list(dict.fromkeys(_INLINE_LINK_RE.findall(text or "")))
    if not found:
        found = list(dict.fromkeys(_BARE_URL_RE.findall(text or "")))
    return found


def extract_claims(answer: str, *, max_claims: int = 12) -> list[tuple[str, list[str]]]:
    """Split answer into atomic claims. Each claim returns (text, cited_urls).

    cited_urls is the list of URLs the claim's `[N](url)` markers point at —
    use those to pick the source the model actually cited, rather than
    falling back to sources[0]. Critical for groundedness accuracy: a model
    that says "$75k ([coindesk](url))" should be checked against coindesk,
    not the first item in its sources array.
    """
    if not answer:
        return []
    cleaned = re.sub(r"^#{1,6}\s*", "", answer, flags=re.MULTILINE)
    cleaned = re.sub(r"\*\*([^*]+)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)
    parts = [p.strip() for p in _SENTENCE_RE.split(cleaned) if p.strip()]
    out: list[tuple[str, list[str]]] = []
    for p in parts:
        urls = _urls_in(p)
        # Stripped-text view for the judge prompt (the [N](url) syntax confuses some judges)
        stripped = re.sub(r"\[([^\]]+)\]\(https?://[^)]+\)", r"\1", p)
        if len(stripped) > 20:
            out.append((stripped, urls))
    return out[:max_claims]


def cited_urls(answer: str) -> list[str]:
    return _urls_in(answer or "")


# ---------------------------------------------------------------------------
# Output normalization — same answers shape across providers.
# ---------------------------------------------------------------------------


def load_provider_outputs(path: Path) -> list[dict[str, Any]]:
    """Load a provider outputs file, normalize to {id, question, category,
    expected_answer, answer, sources, elapsed_seconds, raw, error}."""
    items = json.loads(path.read_text())
    out: list[dict[str, Any]] = []
    for it in items:
        out.append(
            {
                **{
                    k: it.get(k)
                    for k in ("id", "question", "category", "expected_answer", "source")
                    if k in it
                },
                "model": it.get("model", ""),
                "answer": it.get("answer", "") or "",
                "sources": it.get("sources") or [],
                "elapsed_seconds": it.get("elapsed_seconds", 0.0),
                "raw": it.get("raw") or {},
                "error": it.get("error"),
            }
        )
    return out


PROVIDER_NAMES = (
    "desearch",
    "gpt5mini",
    "perplexity",
    "tavily",
    "exa",
)


def discover_providers(run_dir: Path) -> list[str]:
    """Which provider output files exist in run_dir?"""
    return [p for p in PROVIDER_NAMES if (run_dir / f"{p}.json").exists()]


def load_run(run_dir: Path) -> dict[str, list[dict[str, Any]]]:
    """Load all provider outputs in a run directory."""
    return {
        p: load_provider_outputs(run_dir / f"{p}.json")
        for p in discover_providers(run_dir)
    }


# Convenience: timestamp string for new run dirs.
def now_stamp() -> str:
    return time.strftime("%Y-%m-%d")
