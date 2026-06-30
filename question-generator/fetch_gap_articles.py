"""Backfill the article cache for date-bounded gap days using GDELT DOC 2.0."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import crawler as nc  # noqa: E402
import generate_questions as gq  # noqa: E402
from crawler import _canon_url  # noqa: E402
from generate_questions import (  # noqa: E402
    _BANNED_RE,
    _BOILERPLATE_RE,
    _STALE_YEAR_RE,
    _bad_gold,
    MAX_TEXT_CHARS,
    MIN_TEXT_CHARS,
    QUESTIONS_PER_ARTICLE,
    balance,
    dedup,
    gen_for_article,
    quality_grade,
    save_local,
)
from utils import is_contaminated  # noqa: E402

GDELT_DOMAINS: dict[str, str] = {
    "bbc.co.uk": "bbc",
    "theguardian.com": "guardian",
    "aljazeera.com": "aljazeera",
    "npr.org": "npr",
    "nytimes.com": "nyt",
    "news.sky.com": "skynews",
    "dw.com": "dw",
    "france24.com": "france24",
    "independent.co.uk": "independent",
    "nbcnews.com": "nbcnews",
    "cbsnews.com": "cbsnews",
    "abcnews.go.com": "abcnews",
    "reuters.com": "reuters",
    "apnews.com": "apnews",
    "cnn.com": "cnn",
    "cnbc.com": "cnbc",
    "thehill.com": "thehill",
    "politico.com": "politico",
    "time.com": "time",
    "euronews.com": "euronews",
    "latimes.com": "latimes",
    "washingtonpost.com": "wapo",
    "channelnewsasia.com": "cna",
    "straitstimes.com": "straitstimes",
    "japantimes.co.jp": "japantimes",
    "scmp.com": "scmp",
    "timesofindia.indiatimes.com": "toi",
    "thehindu.com": "thehindu",
    "abc.net.au": "abc_au",
    "globalnews.ca": "globalnews",
    "pbs.org": "pbs",
}

UA = nc.UA
GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"

_DROP_SUBSTR = (
    "/sounds/",
    "/av/",
    "/video",
    "/videos/",
    "/live",
    "/live/",
    "/podcasts",
    "/podcast/",
    "/iplayer",
    "/programmes/",
    "/schedule",
    "/weather",
    "/in-pictures",
    "/galleries/",
    "/gallery/",
    "/newsletter",
    "/games/",
    "/crossword",
    "/puzzles/",
    "/audio/",
    "/listen/",
    "/watch/",
)
_DROP_SUFFIX = (".mp3", ".mp4", ".m3u8", ".pdf", ".jpg", ".png")

ARTICLE_CACHE = HERE / "output" / ".cache" / "article"


def _cache_path(url: str) -> Path:
    return ARTICLE_CACHE / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".json")


def _is_article_url(url: str) -> bool:
    """Keep real story URLs, drop media/live/section/homepage/query-only URLs."""
    try:
        s = urllib.parse.urlsplit(url)
    except Exception:
        return False
    if s.scheme not in ("http", "https") or not s.netloc:
        return False

    path = s.path.rstrip("/")
    if not path:
        return False
    low = url.lower()
    if any(frag in low for frag in _DROP_SUBSTR):
        return False
    if any(low.endswith(suf) for suf in _DROP_SUFFIX):
        return False

    segs = [p for p in path.split("/") if p]
    if len(segs) < 2:
        return False
    return True


def _seendate_to_iso(seendate: str) -> str | None:
    try:
        dt = datetime.strptime(seendate, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None
    return dt.isoformat()


def _gdelt_query(
    domain: str, day: datetime, max_records: int, base_delay: float
) -> list[dict]:
    """One day-bounded artlist query for a single domain (with 429 backoff)."""
    params = {
        "query": f"domainis:{domain}",
        "mode": "artlist",
        "maxrecords": str(max_records),
        "format": "json",
        "startdatetime": day.strftime("%Y%m%d000000"),
        "enddatetime": day.strftime("%Y%m%d235959"),
        "sort": "datedesc",
    }
    # Route through FETCH_PROXY with verify off (TLS-intercepting network).
    proxy = os.environ.get("FETCH_PROXY")
    proxies = {"http": proxy, "https": proxy} if proxy else None

    for attempt in range(6):
        try:
            r = requests.get(
                GDELT_URL,
                params=params,
                headers={"User-Agent": UA},
                proxies=proxies,
                timeout=45,
                verify=False,
            )
            if r.status_code == 429:
                time.sleep(base_delay * (attempt + 2))
                continue
            if r.status_code != 200:
                return []
            return r.json().get("articles") or []
        except Exception:
            time.sleep(base_delay + 2 * attempt)
    return []


def discover_day(
    day: datetime, max_records: int, per_day: int, base_delay: float
) -> tuple[list[dict], int, int]:
    """Collect filtered, deduped article items for one day across all domains."""
    label = day.strftime("%Y-%m-%d")
    seen: dict[str, dict] = {}
    found = 0
    for i, (domain, outlet) in enumerate(GDELT_DOMAINS.items(), 1):
        arts = _gdelt_query(domain, day, max_records, base_delay)
        found += len(arts)
        kept_here = 0
        for a in arts:
            raw_url = (a.get("url") or "").strip()
            if not raw_url or not _is_article_url(raw_url):
                continue
            url = _canon_url(raw_url)
            if url in seen:
                continue
            iso = _seendate_to_iso(a.get("seendate", ""))
            if not iso:
                continue
            seen[url] = {
                "url": url,
                "title": (a.get("title") or "").strip(),
                "published": iso,
                "source": f"sm:{outlet}",
            }
            kept_here += 1
        print(
            f"    {label} [{i:>2}/{len(GDELT_DOMAINS)}] {domain:<28} "
            f"got={len(arts):>3} kept+={kept_here:>3} total_kept={len(seen)}",
            flush=True,
        )
        time.sleep(base_delay)

    items = list(seen.values())
    items.sort(key=lambda it: it["published"], reverse=True)

    return items[:per_day], found, len(seen)


async def fetch_bodies(
    items: list[dict], concurrency: int, timeout: float
) -> tuple[int, int]:
    """Fetch each article body via the existing crawler and write cache records. Returns (ok, failed)."""
    sem = asyncio.Semaphore(concurrency)
    ok = 0
    failed = 0

    async with aiohttp.ClientSession() as session:

        async def one(it: dict):
            nonlocal ok, failed
            cp = _cache_path(it["url"])
            if cp.exists():
                try:
                    prior = json.loads(cp.read_text())
                except Exception:
                    prior = None
                if prior:
                    ok += 1
                else:
                    failed += 1
                return

            async with sem:
                text = await nc.fetch_text(session, it["url"], timeout)
            if text:
                text = _BOILERPLATE_RE.sub("", text).strip()

            rec = None
            if text and MIN_TEXT_CHARS <= len(text):
                rec = {
                    "url": it["url"],
                    "title": it["title"],
                    "text": text[:MAX_TEXT_CHARS],
                    "published": it["published"],
                    "source": it["source"],
                }
            cp.parent.mkdir(parents=True, exist_ok=True)
            cp.write_text(json.dumps(rec or {}, ensure_ascii=False))
            if rec:
                ok += 1
            else:
                failed += 1

        await asyncio.gather(*[one(it) for it in items])

    return ok, failed


def _daterange(start: datetime, end: datetime):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


async def main_async(args) -> int:
    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    print(
        f"[gap] GDELT backfill {args.start}..{args.end} across {len(GDELT_DOMAINS)} domains",
        flush=True,
    )
    grand_ok = 0
    for day in _daterange(start, end):
        label = day.strftime("%Y-%m-%d")
        items, found, kept = discover_day(
            day, args.max_records, args.per_day, args.delay
        )
        ok, failed = await fetch_bodies(items, args.concurrency, args.fetch_timeout)
        grand_ok += ok
        print(
            f"  {label}: GDELT found={found}  kept(filtered+deduped+capped)={len(items)}"
            f"  bodies OK={ok}  failed={failed}",
            flush=True,
        )

    print(f"[gap] done — {grand_ok} article bodies cached into {ARTICLE_CACHE}")
    return 0


def load_cached_by_day(start: datetime, end: datetime) -> dict[str, list[dict]]:
    """Bucket usable cached articles by published day within [start, end]."""
    lo, hi = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    by_day: dict[str, list[dict]] = {}
    for fp in ARTICLE_CACHE.glob("*.json"):
        try:
            rec = json.loads(fp.read_text())
        except Exception:
            continue
        if not rec or not rec.get("text"):
            continue
        day = (rec.get("published") or "")[:10]
        if not day or day < lo or day > hi:
            continue
        by_day.setdefault(day, []).append(rec)

    for arts in by_day.values():
        arts.sort(key=lambda r: r.get("published", ""), reverse=True)

    return by_day


async def generate_day(
    articles: list[dict], date: str, out: Path, target: int, gen_concurrency: int
) -> int:
    """Invert cached articles for one day through the existing pipeline → save_local."""
    sem = asyncio.Semaphore(gen_concurrency)

    async def gen(a: dict) -> list[dict]:
        cached = gq._cache_get("gen", a["url"])
        if cached is not None:
            return cached
        async with sem:
            qs = await gen_for_article(a, QUESTIONS_PER_ARTICLE)
        gq._cache_put("gen", a["url"], qs)
        return qs

    raw: list[dict] = []
    for batch in await asyncio.gather(*[gen(a) for a in articles]):
        raw.extend(batch)

    clean = [
        q
        for q in raw
        if not is_contaminated(q["source_url"], q["question"], q["gold_answer"])
        and 15 <= len(q["question"]) <= 300
        and not _BANNED_RE.search(q["question"])
        and not _STALE_YEAR_RE.search(q["question"])
        and not _STALE_YEAR_RE.search(q["gold_answer"])
        and not _bad_gold(
            q["question"], q["gold_answer"], q.get("answer_type", "short")
        )
    ]
    unique = await dedup(clean)
    graded = await quality_grade(unique)
    final = balance(graded, target)

    save_local(out, date, final)
    print(
        f"  {date}: {len(articles)} articles → {len(raw)} raw → {len(final)} saved",
        flush=True,
    )

    return len(final)


async def generate_async(args) -> int:
    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    out = Path(args.out)
    if args.cache:
        gq._CACHE_ROOT = out / ".cache"

    by_day = load_cached_by_day(start, end)
    print(f"[gap-gen] cached usable articles {args.start}..{args.end}:", flush=True)
    for day in sorted(by_day):
        n = len(by_day[day])
        cap = f"  (capped to {args.limit})" if args.limit and n > args.limit else ""
        print(f"    {day}: {n} articles{cap}", flush=True)
    if not by_day:
        print(
            "  none — run the fetch step first (drop --generate) to cache gap-day bodies."
        )
        return 1

    if args.dry_run:
        s = next(iter(by_day.values()))[0]
        print(
            f"[gap-gen] dry-run OK (no LLM). sample: {s['source']} {s['published'][:10]} {s['url']}"
        )
        return 0

    total = 0
    for day in sorted(by_day):
        arts = by_day[day][: args.limit] if args.limit else by_day[day]
        total += await generate_day(arts, day, out, args.target, args.gen_concurrency)

    print(
        f"[gap-gen] done — {total} questions across {len(by_day)} day(s). "
        f"Next: python3 to_hf_dataset.py --label <day> --only <day>"
    )
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--start", default="2026-06-17", help="first gap day (YYYY-MM-DD)")
    p.add_argument(
        "--end", default="2026-06-23", help="last gap day inclusive (YYYY-MM-DD)"
    )
    p.add_argument(
        "--per-day", type=int, default=400, help="max article URLs kept per day"
    )
    p.add_argument(
        "--max-records", type=int, default=250, help="GDELT maxrecords per domain/day"
    )
    p.add_argument("--concurrency", type=int, default=24, help="body-fetch concurrency")
    p.add_argument("--fetch-timeout", type=float, default=20.0)
    p.add_argument(
        "--delay",
        type=float,
        default=5.0,
        help="seconds between GDELT requests (raise if the free API returns 429)",
    )

    p.add_argument(
        "--generate",
        action="store_true",
        help="invert already-cached gap-day articles into questions (needs OPENAI_API_KEY)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="with --generate: only show per-day cached-article counts, no LLM",
    )
    p.add_argument("--cache", action="store_true", help="use the resumable gen cache")
    p.add_argument(
        "--out", default="output", help="output dir (questions/ + golds/ + .cache/)"
    )
    p.add_argument(
        "--target", type=int, default=150, help="questions kept per day after balancing"
    )
    p.add_argument(
        "--gen-concurrency",
        type=int,
        default=8,
        help="concurrent gen_for_article calls",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=0,
        help="cap articles inverted per day (0 = no cap)",
    )

    args = p.parse_args()

    if args.generate:
        return asyncio.run(generate_async(args))

    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
