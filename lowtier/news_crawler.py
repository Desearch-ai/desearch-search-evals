"""Standing news crawler — discover recent article URLs BLIND to the questions.

Unlike the earlier rolling crawl (RSS *snapshots* only, ~1,651 articles), this
adds the high-volume lever: **news sitemaps**. Outlet `news-sitemap.xml` /
sitemap-index files list every article published in the last ~48h with a
`news:publication_date`, so one outlet yields hundreds–thousands of fresh URLs
instead of an RSS feed's ~30. We combine sitemaps + full RSS across ~40 trusted
outlets, keep the last ~1-2 weeks, fetch full text through the stable serp-api
`/v1/content` endpoint (trafilatura fallback), dedup, and persist.

This is deliberately NOT derived from the benchmark questions — it crawls
whatever the outlets published. Output is a held-out corpus we can honestly test
against the 50 ab_baseline questions.

Output: lowtier/index_week/corpus.jsonl — {url, title, text, published_at, source}

Usage:
  python lowtier/news_crawler.py
  python lowtier/news_crawler.py --max-articles 9000 --time-budget 1200 --concurrency 16
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import socket
import sys
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import aiohttp

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from providers.common import load_env  # noqa: E402

load_env()

import feedparser  # noqa: E402
import trafilatura  # noqa: E402
from lxml import etree  # noqa: E402

# Reuse the validated RSS feed list from the rolling crawl (NOT question-derived).
from lowtier.build_rolling_corpus import FEEDS  # noqa: E402

OUT_DEFAULT = HERE / "index_week" / "corpus.jsonl"
SERP_CONTENT_URL = "http://localhost:3001/v1/content"
MIN_CHARS = 200
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_0) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

SM_NS = {
    "s": "http://www.sitemaps.org/schemas/sitemap/0.9",
    "n": "http://www.google.com/schemas/sitemap-news/0.9",
}

# News sitemaps (or sitemap-index files) confirmed reachable. An index is
# expanded ONE level: we pull its child sitemaps (capped) and read article URLs
# from those. Each yields hundreds–thousands of last-48h articles.
SITEMAPS: dict[str, str] = {
    "bbc": "https://www.bbc.com/sitemaps/https-index-com-news.xml",
    "guardian": "https://www.theguardian.com/sitemaps/news.xml",
    "nyt": "https://www.nytimes.com/sitemaps/new/news.xml.gz",
    "reuters": "https://www.reuters.com/arc/outboundfeeds/news-sitemap-index/?outputType=xml",
    "aljazeera": "https://www.aljazeera.com/news-sitemap.xml",
    "cnbc": "https://www.cnbc.com/sitemapAll.xml",
    "apnews": "https://apnews.com/news-sitemap-content.xml",
    "thehill": "https://thehill.com/news-sitemap.xml",
    "time": "https://time.com/news-sitemap.xml",
    "techcrunch": "https://techcrunch.com/news-sitemap.xml",
}

# How many child sitemaps to expand from each index (most-recent first as the
# outlet orders them), and how many article URLs to keep per source from
# sitemaps before the global round-robin cap.
MAX_CHILD_SITEMAPS = 8
PER_SOURCE_SITEMAP_CAP = 1500


def _serp_token() -> str | None:
    p = Path("/Users/mirian/Documents/serp-api/.env")
    if not p.exists():
        return None
    for line in p.read_text().splitlines():
        line = line.strip()
        if line.startswith("API_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def _canon_url(url: str) -> str:
    """Strip fragments + common tracking query noise so dedupe is stable."""
    try:
        s = urlsplit(url)
    except Exception:
        return url
    keep = []
    for kv in s.query.split("&"):
        if not kv:
            continue
        key = kv.split("=", 1)[0].lower()
        if key.startswith("utm_") or key in {
            "at_medium", "at_campaign", "at_custom1", "at_custom2",
            "at_custom3", "at_custom4", "at_bbc_team", "ito", "cmp",
            "ref", "fbclid", "gclid", "mc_cid", "mc_eid", "smid",
        }:
            continue
        keep.append(kv)
    return urlunsplit((s.scheme, s.netloc, s.path.rstrip("/") or "/",
                       "&".join(keep), ""))


_LINE_SEP_FIX = {
    " ": "\n", " ": "\n", "\x85": "\n",
    "\x0b": " ", "\x0c": " ", "\x1c": " ", "\x1d": " ", "\x1e": " ",
    "\r": "\n",
}
_LINE_SEP_TABLE = {ord(k): v for k, v in _LINE_SEP_FIX.items()}


def _sanitize(text: str) -> str:
    return text.translate(_LINE_SEP_TABLE)


def _parse_iso(raw: str | None) -> datetime | None:
    if not raw:
        return None
    raw = raw.strip()
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        pass
    try:
        return parsedate_to_datetime(raw)
    except Exception:
        return None


def _entry_date(entry) -> str | None:
    for attr in ("published_parsed", "updated_parsed"):
        st = getattr(entry, attr, None)
        if st:
            try:
                return datetime(*st[:6], tzinfo=timezone.utc).isoformat()
            except Exception:
                pass
    for attr in ("published", "updated", "pubDate"):
        raw = getattr(entry, attr, None)
        if raw:
            dt = _parse_iso(raw)
            if dt:
                return dt.astimezone(timezone.utc).isoformat()
    return None


# --------------------------------------------------------------------------
# Sitemap discovery (sync — runs once up front, fast).
# --------------------------------------------------------------------------

def _http_get(url: str, timeout: float = 20.0) -> bytes:
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    raw = urllib.request.urlopen(req, timeout=timeout).read()
    if url.endswith(".gz") or raw[:2] == b"\x1f\x8b":
        try:
            raw = gzip.decompress(raw)
        except Exception:
            pass
    return raw


def _parse_sitemap(raw: bytes) -> tuple[str, list[tuple[str, str | None]]]:
    """Return (kind, items). kind is 'index' (items=child sitemap URLs, no date)
    or 'urlset' (items=[(article_url, news_pub_date_iso|None)])."""
    try:
        root = etree.fromstring(raw)
    except Exception:
        return "?", []
    tag = etree.QName(root).localname if root is not None else ""
    if tag == "sitemapindex":
        out = []
        for loc in root.findall(".//s:sitemap/s:loc", SM_NS):
            if loc.text:
                out.append((loc.text.strip(), None))
        return "index", out
    if tag == "urlset":
        out = []
        for u in root.findall(".//s:url", SM_NS):
            loc = u.findtext("s:loc", namespaces=SM_NS)
            if not loc:
                continue
            pd = (u.findtext(".//n:publication_date", namespaces=SM_NS)
                  or u.findtext("s:lastmod", namespaces=SM_NS))
            out.append((loc.strip(), pd.strip() if pd else None))
        return "urlset", out
    return "?", []


def collect_sitemap_items(cutoff: datetime) -> list[dict]:
    """Walk each configured sitemap (expanding an index one level) and return
    [{url, title, source, feed_date}] for articles newer than cutoff (or undated
    — undated URLs from a *news* sitemap are still recent by construction)."""
    socket.setdefaulttimeout(20)
    seen: dict[str, dict] = {}
    for source, sm_url in SITEMAPS.items():
        kept = 0
        try:
            raw = _http_get(sm_url)
        except Exception as e:  # noqa: BLE001
            print(f"  [sitemap err] {source}: {type(e).__name__}: {str(e)[:60]}")
            continue
        kind, items = _parse_sitemap(raw)
        targets: list[tuple[str, str | None]] = []
        if kind == "index":
            children = [u for u, _ in items][:MAX_CHILD_SITEMAPS]
            for child in children:
                try:
                    craw = _http_get(child)
                except Exception:
                    continue
                ckind, citems = _parse_sitemap(craw)
                if ckind == "urlset":
                    targets.extend(citems)
        elif kind == "urlset":
            targets = items
        else:
            print(f"  [sitemap skip] {source}: unrecognized root ({kind})")
            continue

        for loc, pd in targets:
            if kept >= PER_SOURCE_SITEMAP_CAP:
                break
            if not loc.startswith("http") or "news.google.com" in loc:
                continue
            dt = _parse_iso(pd)
            if dt is not None and dt.astimezone(timezone.utc) < cutoff:
                continue
            url = _canon_url(loc)
            if url in seen:
                continue
            seen[url] = {
                "url": url, "title": "",
                "source": f"sm:{source}",
                "feed_date": (dt.astimezone(timezone.utc).isoformat()
                              if dt else None),
            }
            kept += 1
        print(f"  sitemap {source}: kind={kind} kept={kept}")
    print(f"Sitemaps: {len(seen)} unique recent article URLs")
    return list(seen.values())


def collect_feed_items() -> list[dict]:
    """Full RSS across the trusted-outlet feed list (reused, NOT question-derived)."""
    socket.setdefaulttimeout(12)
    seen: dict[str, dict] = {}
    ok = 0
    for source, feed_url in FEEDS.items():
        try:
            parsed = feedparser.parse(feed_url)
        except Exception:
            continue
        added = 0
        for entry in parsed.entries:
            link = getattr(entry, "link", "") or ""
            if not link.startswith("http") or "news.google.com" in link:
                continue
            url = _canon_url(link)
            if url in seen:
                continue
            seen[url] = {
                "url": url,
                "title": (getattr(entry, "title", "") or "").strip(),
                "source": f"rss:{source}",
                "feed_date": _entry_date(entry),
            }
            added += 1
        if added:
            ok += 1
    print(f"RSS: {ok}/{len(FEEDS)} non-empty feeds · {len(seen)} unique URLs")
    return list(seen.values())


def merge_dedupe(*lists: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for lst in lists:
        for it in lst:
            if it["url"] not in seen:
                seen[it["url"]] = it
    return list(seen.values())


# --------------------------------------------------------------------------
# Fetch (async).
# --------------------------------------------------------------------------

async def fetch_via_serp(session: aiohttp.ClientSession, url: str, token: str,
                         timeout: float) -> tuple[str, str | None]:
    body = {"url": url, "format": "text", "js": False}
    try:
        async with session.post(
            SERP_CONTENT_URL, json=body,
            headers={"Authorization": f"Bearer {token}"},
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            if resp.status != 200:
                return "", None
            payload = await resp.json(content_type=None)
    except Exception:
        return "", None
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return "", None
    return (data.get("content") or "").strip(), data.get("publishedTime")


async def fetch_via_trafilatura(session: aiohttp.ClientSession, url: str,
                                timeout: float, proxy: str | None = None) -> str:
    try:
        async with session.get(
            url, headers={"User-Agent": UA, "Accept": "text/html"},
            timeout=aiohttp.ClientTimeout(total=timeout), allow_redirects=True,
            proxy=proxy,
        ) as resp:
            ctype = resp.headers.get("content-type", "").lower()
            if "html" not in ctype and "text" not in ctype:
                return ""
            html = await resp.text(errors="ignore")
    except Exception:
        return ""
    if not html:
        return ""
    return (trafilatura.extract(
        html, include_comments=False, include_tables=True,
        favor_recall=True, url=url,
    ) or "").strip()


def _round_robin(items: list[dict], cap: int) -> list[dict]:
    by_src: dict[str, list[dict]] = {}
    for it in items:
        by_src.setdefault(it["source"], []).append(it)
    ordered: list[dict] = []
    queues = list(by_src.values())
    i = 0
    while queues and len(ordered) < cap:
        q = queues[i % len(queues)]
        ordered.append(q.pop(0))
        if not q:
            queues.remove(q)
        else:
            i += 1
    return ordered


async def build(out_path: Path, max_articles: int, concurrency: int,
                time_budget: float, fetch_timeout: float, lookback_days: int) -> None:
    t0 = time.monotonic()
    token = _serp_token()
    if not token:
        print("WARNING: no serp-api API_TOKEN found — using trafilatura only.")

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    print(f"Discovery (lookback {lookback_days}d, cutoff {cutoff.date()}):")
    sm_items = collect_sitemap_items(cutoff)
    feed_items = collect_feed_items()
    items = merge_dedupe(sm_items, feed_items)
    print(f"Merged unique URLs: {len(items)} "
          f"(sitemaps {len(sm_items)} + rss {len(feed_items)})")

    if len(items) > max_articles:
        items = _round_robin(items, max_articles)
        print(f"Capped to {len(items)} articles (round-robin across sources).")

    print(f"Fetching {len(items)} articles via serp-api "
          f"(concurrency={concurrency}, budget={time_budget:.0f}s)…")

    sem = asyncio.Semaphore(concurrency)
    deadline = t0 + time_budget
    stats = {"serp": 0, "trafi": 0, "short": 0, "fail": 0, "skipped": 0}
    done = 0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    _sink = out_path.open("w")  # incremental flush so a long run is observable
    _sink_lock = asyncio.Lock()

    connector = aiohttp.TCPConnector(limit=concurrency + 8, ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        async def one(item: dict) -> dict | None:
            nonlocal done
            if time.monotonic() > deadline:
                stats["skipped"] += 1
                return None
            async with sem:
                if time.monotonic() > deadline:
                    stats["skipped"] += 1
                    return None
                text, pub = "", None
                if token:
                    text, pub = await fetch_via_serp(session, item["url"], token,
                                                     fetch_timeout)
                src_kind = "serp"
                if len(text) < MIN_CHARS:
                    fb = await fetch_via_trafilatura(session, item["url"],
                                                     fetch_timeout)
                    if len(fb) >= len(text):
                        text, src_kind = fb, "trafi"
            done += 1
            if done % 200 == 0:
                el = time.monotonic() - t0
                print(f"  fetched {done}/{len(items)} @ {el:.0f}s "
                      f"(serp={stats['serp']} trafi={stats['trafi']} "
                      f"short={stats['short']} fail={stats['fail']})")
            if not text:
                stats["fail"] += 1
                return None
            if len(text) < MIN_CHARS:
                stats["short"] += 1
                return None
            stats[src_kind] += 1
            published = pub or item["feed_date"]
            rec = {
                "url": item["url"],
                "title": _sanitize(item["title"]),
                "text": _sanitize(text),
                "published_at": published or "",
                "source": item["source"],
            }
            async with _sink_lock:
                _sink.write(json.dumps(rec, ensure_ascii=False) + "\n")
                _sink.flush()
            return rec

        results = await asyncio.gather(*[one(it) for it in items])
    _sink.close()
    records = [r for r in results if r]

    chars = sum(len(r["text"]) for r in records)
    dated = [r["published_at"][:10] for r in records if r["published_at"]]
    dmin = min(dated) if dated else "?"
    dmax = max(dated) if dated else "?"
    elapsed = time.monotonic() - t0
    n_sm = sum(1 for r in records if r["source"].startswith("sm:"))
    n_rss = sum(1 for r in records if r["source"].startswith("rss:"))
    print(f"\n=== Standing news corpus built in {elapsed:.1f}s ===")
    print(f"  usable articles: {len(records)} (sitemap-sourced {n_sm} · rss {n_rss})")
    print(f"  extraction: serp-api={stats['serp']} trafilatura={stats['trafi']} "
          f"| dropped: short<{MIN_CHARS}={stats['short']} "
          f"empty/fail={stats['fail']} over-budget-skipped={stats['skipped']}")
    print(f"  date range (published_at): {dmin} .. {dmax}  "
          f"({len(dated)}/{len(records)} dated)")
    print(f"  total text: {chars:,} chars (~{chars // 4:,} tokens est)")
    print(f"  distinct sources: {len({r['source'] for r in records})}")
    print(f"  wrote {out_path}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path, default=OUT_DEFAULT)
    p.add_argument("--max-articles", type=int, default=9000)
    p.add_argument("--concurrency", type=int, default=16)
    p.add_argument("--time-budget", type=float, default=1200.0,
                   help="hard wall-clock cap on fetching, seconds (~20 min)")
    p.add_argument("--fetch-timeout", type=float, default=45.0)
    p.add_argument("--lookback-days", type=int, default=14)
    args = p.parse_args()
    asyncio.run(build(args.out, args.max_articles, args.concurrency,
                      args.time_budget, args.fetch_timeout, args.lookback_days))
