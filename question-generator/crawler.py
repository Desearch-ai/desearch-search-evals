"""Gather recent article URLs (RSS + news sitemaps) and fetch their full text.

This is deliberately NOT derived from any benchmark questions — it crawls
whatever the outlets in sources.py published. Text extraction is a plain HTTP
GET + trafilatura (no third-party scraping service), with an optional outbound
HTTP proxy (FETCH_PROXY) for sites that rate-limit by IP.
"""

from __future__ import annotations

import gzip
import os
import socket
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit, urlunsplit

import aiohttp
import feedparser
import trafilatura
from lxml import etree

from sources import FEEDS, SITEMAPS

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_0) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

SM_NS = {
    "s": "http://www.sitemaps.org/schemas/sitemap/0.9",
    "n": "http://www.google.com/schemas/sitemap-news/0.9",
}

# Sitemap depth: child sitemaps expanded per index, and URLs kept per source.
# Raise (env) for a historical backfill that needs to reach weeks/months back.
MAX_CHILD_SITEMAPS = int(os.environ.get("MAX_CHILD_SITEMAPS", "8"))
PER_SOURCE_SITEMAP_CAP = int(os.environ.get("PER_SOURCE_SITEMAP_CAP", "1500"))


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
            "at_medium",
            "at_campaign",
            "at_custom1",
            "at_custom2",
            "at_custom3",
            "at_custom4",
            "at_bbc_team",
            "ito",
            "cmp",
            "ref",
            "fbclid",
            "gclid",
            "mc_cid",
            "mc_eid",
            "smid",
        }:
            continue
        keep.append(kv)
    return urlunsplit(
        (s.scheme, s.netloc, s.path.rstrip("/") or "/", "&".join(keep), "")
    )


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
            pd = u.findtext(".//n:publication_date", namespaces=SM_NS) or u.findtext(
                "s:lastmod", namespaces=SM_NS
            )
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
                "url": url,
                "title": "",
                "source": f"sm:{source}",
                "feed_date": (dt.astimezone(timezone.utc).isoformat() if dt else None),
            }
            kept += 1
        print(f"  sitemap {source}: kind={kind} kept={kept}")
    print(f"Sitemaps: {len(seen)} unique recent article URLs")
    return list(seen.values())


def collect_feed_items() -> list[dict]:
    """Full RSS/Atom across the trusted-outlet feed list (NOT question-derived)."""
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


def round_robin(items: list[dict], cap: int) -> list[dict]:
    """Interleave items across sources so no single outlet dominates the queue."""
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


async def fetch_text(
    session: aiohttp.ClientSession, url: str, timeout: float, proxy: str | None = None
) -> str:
    """GET the page and extract readable article text with trafilatura."""
    try:
        async with session.get(
            url,
            headers={"User-Agent": UA, "Accept": "text/html"},
            timeout=aiohttp.ClientTimeout(total=timeout),
            allow_redirects=True,
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
    return (
        trafilatura.extract(
            html,
            include_comments=False,
            include_tables=True,
            favor_recall=True,
            url=url,
        )
        or ""
    ).strip()
