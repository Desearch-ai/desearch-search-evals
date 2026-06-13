"""Stage 1 (rolling) — build a BROAD rolling-news corpus from news SOURCES.

This is deliberately NOT derived from the benchmark questions. We crawl a wide
set of major outlets' RSS feeds (world/politics/business/tech/science/sport/
culture), collect article URLs + pubDate, then fetch full article text through
the stable serp-api `/v1/content` endpoint (falling back to trafilatura on an
empty extraction). The result is a topic-broad snapshot of ~the last 1-2 weeks
of news that we can honestly test against held-out questions.

Output: lowtier/index_rolling/corpus_rolling.jsonl — one JSON object per page:
  {url, title, text, published_at, source}

Usage:
  python lowtier/build_rolling_corpus.py
  python lowtier/build_rolling_corpus.py --max-articles 2500 --time-budget 600
"""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
import sys
import time
from datetime import datetime, timezone
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

OUT_DEFAULT = HERE / "index_rolling" / "corpus_rolling.jsonl"
SERP_CONTENT_URL = "http://localhost:3001/v1/content"
MIN_CHARS = 200
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_0) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# Direct-outlet RSS feeds only — these yield REAL article URLs. Google News RSS
# was deliberately excluded: its /rss/articles/ links are an undecodable
# base64-protobuf redirect that the content endpoint can't resolve. The list
# spans many outlets x many sections to get a broad topic mix.
FEEDS: dict[str, str] = {
    # --- World / general / wire ---
    "bbc_world": "http://feeds.bbci.co.uk/news/world/rss.xml",
    "bbc_top": "http://feeds.bbci.co.uk/news/rss.xml",
    "bbc_us_canada": "http://feeds.bbci.co.uk/news/world/us_and_canada/rss.xml",
    "bbc_europe": "http://feeds.bbci.co.uk/news/world/europe/rss.xml",
    "bbc_asia": "http://feeds.bbci.co.uk/news/world/asia/rss.xml",
    "bbc_mideast": "http://feeds.bbci.co.uk/news/world/middle_east/rss.xml",
    "bbc_africa": "http://feeds.bbci.co.uk/news/world/africa/rss.xml",
    "guardian_world": "https://www.theguardian.com/world/rss",
    "guardian_us": "https://www.theguardian.com/us-news/rss",
    "guardian_europe": "https://www.theguardian.com/world/europe-news/rss",
    "guardian_americas": "https://www.theguardian.com/world/americas/rss",
    "guardian_asia": "https://www.theguardian.com/world/asia/rss",
    "guardian_mideast": "https://www.theguardian.com/world/middleeast/rss",
    "guardian_africa": "https://www.theguardian.com/world/africa/rss",
    "guardian_global_dev": "https://www.theguardian.com/global-development/rss",
    "aljazeera_all": "https://www.aljazeera.com/xml/rss/all.xml",
    "npr_news": "https://feeds.npr.org/1001/rss.xml",
    "npr_world": "https://feeds.npr.org/1004/rss.xml",
    "skynews_world": "https://feeds.skynews.com/feeds/rss/world.xml",
    "skynews_home": "https://feeds.skynews.com/feeds/rss/home.xml",
    "dw_all": "https://rss.dw.com/rdf/rss-en-all",
    "france24": "https://www.france24.com/en/rss",
    "independent_world": "https://www.independent.co.uk/news/world/rss",
    "nyt_world": "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
    "nyt_home": "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",
    "nyt_americas": "https://rss.nytimes.com/services/xml/rss/nyt/Americas.xml",
    "nyt_europe": "https://rss.nytimes.com/services/xml/rss/nyt/Europe.xml",
    "nyt_asia": "https://rss.nytimes.com/services/xml/rss/nyt/AsiaPacific.xml",
    "nyt_mideast": "https://rss.nytimes.com/services/xml/rss/nyt/MiddleEast.xml",
    "nyt_africa": "https://rss.nytimes.com/services/xml/rss/nyt/Africa.xml",
    "nbc_world": "https://feeds.nbcnews.com/nbcnews/public/world",
    "nbc_top": "https://feeds.nbcnews.com/nbcnews/public/news",
    "cbsnews": "https://www.cbsnews.com/latest/rss/main",
    "abc_intl": "https://abcnews.go.com/abcnews/internationalheadlines",
    "abc_top": "https://feeds.abcnews.com/abcnews/topstories",
    # --- Politics ---
    "guardian_politics": "https://www.theguardian.com/politics/rss",
    "thehill": "https://thehill.com/news/feed/",
    "politico": "https://rss.politico.com/politics-news.xml",
    "npr_politics": "https://feeds.npr.org/1014/rss.xml",
    "nyt_politics": "https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml",
    "axios": "https://api.axios.com/feed/",
    "vox": "https://www.vox.com/rss/index.xml",
    "time": "https://time.com/feed/",
    # --- Business / economy ---
    "cnbc_top": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
    "cnbc_econ": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258",
    "guardian_business": "https://www.theguardian.com/uk/business/rss",
    "guardian_money": "https://www.theguardian.com/money/rss",
    "bbc_business": "http://feeds.bbci.co.uk/news/business/rss.xml",
    "marketwatch_top": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    "nyt_business": "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",
    "nyt_economy": "https://rss.nytimes.com/services/xml/rss/nyt/Economy.xml",
    "npr_business": "https://feeds.npr.org/1006/rss.xml",
    "ft_home": "https://www.ft.com/rss/home",
    "economist_finance": "https://www.economist.com/finance-and-economics/rss.xml",
    "economist_intl": "https://www.economist.com/international/rss.xml",
    # --- Tech ---
    "theverge": "https://www.theverge.com/rss/index.xml",
    "techcrunch": "https://techcrunch.com/feed/",
    "arstechnica": "https://feeds.arstechnica.com/arstechnica/index",
    "wired": "https://www.wired.com/feed/rss",
    "guardian_tech": "https://www.theguardian.com/uk/technology/rss",
    "bbc_tech": "http://feeds.bbci.co.uk/news/technology/rss.xml",
    "cnbc_tech": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=19854910",
    "nyt_tech": "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml",
    "engadget": "https://www.engadget.com/rss.xml",
    # --- Science / health / environment ---
    "guardian_science": "https://www.theguardian.com/science/rss",
    "guardian_environment": "https://www.theguardian.com/environment/rss",
    "bbc_science": "http://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
    "bbc_health": "http://feeds.bbci.co.uk/news/health/rss.xml",
    "npr_science": "https://feeds.npr.org/1007/rss.xml",
    "npr_health": "https://feeds.npr.org/1128/rss.xml",
    "sciencedaily": "https://www.sciencedaily.com/rss/top/science.xml",
    "nature_news": "https://www.nature.com/nature.rss",
    "phys_org": "https://phys.org/rss-feed/",
    "scientificamerican": "http://rss.sciam.com/ScientificAmerican-Global",
    "spacecom": "https://www.space.com/feeds/all",
    "nyt_science": "https://rss.nytimes.com/services/xml/rss/nyt/Science.xml",
    "nyt_health": "https://rss.nytimes.com/services/xml/rss/nyt/Health.xml",
    "nyt_climate": "https://rss.nytimes.com/services/xml/rss/nyt/Climate.xml",
    # --- Sport / culture (breadth) ---
    "bbc_sport": "http://feeds.bbci.co.uk/sport/rss.xml",
    "guardian_sport": "https://www.theguardian.com/uk/sport/rss",
    "guardian_football": "https://www.theguardian.com/football/rss",
    "espn_top": "https://www.espn.com/espn/rss/news",
    "guardian_film": "https://www.theguardian.com/film/rss",
    "guardian_music": "https://www.theguardian.com/music/rss",
    "guardian_books": "https://www.theguardian.com/books/rss",
    "bbc_entertainment": "http://feeds.bbci.co.uk/news/entertainment_and_arts/rss.xml",
    "nyt_movies": "https://rss.nytimes.com/services/xml/rss/nyt/Movies.xml",

    # --- expanded distinct-publisher feeds (added for daily volume + diversity) ---
    "semafor": "https://www.semafor.com/rss.xml",
    "newscientist": "https://www.newscientist.com/feed/home/",
    "mashable": "https://mashable.com/feeds/rss/all",
    "ninetofivemac": "https://9to5mac.com/feed/",
    "latimes_world": "https://www.latimes.com/world-nation/rss2.0.xml",
    "latimes_biz": "https://www.latimes.com/business/rss2.0.xml",
    "euronews": "https://www.euronews.com/rss",
    "scmp_news": "https://www.scmp.com/rss/91/feed",
    "theregister": "https://www.theregister.com/headlines.atom",
    "livescience": "https://www.livescience.com/feeds/all",
    "theconversation": "https://theconversation.com/global/articles.atom",
    "straitstimes": "https://www.straitstimes.com/news/world/rss.xml",
    "toi_top": "https://timesofindia.indiatimes.com/rssfeedstopstories.cms",
    "cbssports": "https://www.cbssports.com/rss/headlines/",
    "japantimes": "https://www.japantimes.co.jp/feed/",
    "quartz": "https://qz.com/rss",
    "theatlantic": "https://www.theatlantic.com/feed/all/",
    "thedailybeast": "https://www.thedailybeast.com/arc/outboundfeeds/rss/",
    "france24": "https://www.france24.com/en/rss",
    "defenseone": "https://www.defenseone.com/rss/all/",
    "cna": "https://www.channelnewsasia.com/rssfeeds/8395986",
    "zdnet": "https://www.zdnet.com/news/rss.xml",
    "businessinsider": "https://www.businessinsider.com/rss",
    "propublica": "https://www.propublica.org/feeds/propublica/main",
    "skysports": "https://www.skysports.com/rss/12040",
    "gizmodo": "https://gizmodo.com/rss",
    "newsweek": "https://www.newsweek.com/rss",
    "sciencenews": "https://www.sciencenews.org/feed",
    "toi_israel": "https://www.timesofisrael.com/feed/",
    "fortune": "https://fortune.com/feed/",
    "androidpolice": "https://www.androidpolice.com/feed/",
    "variety": "https://variety.com/feed/",
    "thr": "https://www.hollywoodreporter.com/feed/",
    "billboard": "https://www.billboard.com/feed/",
    "rollingstone": "https://www.rollingstone.com/feed/",
    "politico_eu": "https://www.politico.eu/feed/",
    "smithsonian": "https://www.smithsonianmag.com/rss/latest_articles/",
    "wapo_world": "https://feeds.washingtonpost.com/rss/world",
    "venturebeat": "https://venturebeat.com/feed/",
}


def _serp_token() -> str | None:
    """Read API_TOKEN from the serp-api .env. Returns None if absent."""
    for p in (Path("/Users/mirian/Documents/serp-api/.env"),):
        if not p.exists():
            continue
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


# Chars that str.splitlines() treats as line breaks but json.dumps does NOT
# escape — they'd split a JSONL record when a reader uses .splitlines()
# (build_index.py does). Normalize them to spaces / \n so each record stays
# one physical line.
_LINE_SEP_FIX = {
    " ": "\n", " ": "\n", "\x85": "\n",  # LS, PS, NEL
    "\x0b": " ", "\x0c": " ", "\x1c": " ", "\x1d": " ", "\x1e": " ",
    "\r": "\n",
}
_LINE_SEP_TABLE = {ord(k): v for k, v in _LINE_SEP_FIX.items()}


def _sanitize(text: str) -> str:
    """Strip exotic line-break chars so the row survives str.splitlines()."""
    return text.translate(_LINE_SEP_TABLE)


def _entry_date(entry) -> str | None:
    """ISO date from a feedparser entry's published/updated struct or string."""
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
            try:
                return parsedate_to_datetime(raw).astimezone(timezone.utc).isoformat()
            except Exception:
                pass
    return None


def collect_feed_items() -> list[dict]:
    """Parse every feed; return deduped [{url, title, source, feed_date}]."""
    socket.setdefaulttimeout(10)
    seen: dict[str, dict] = {}
    per_feed: list[tuple[str, int]] = []
    for source, feed_url in FEEDS.items():
        try:
            parsed = feedparser.parse(feed_url)
        except Exception as e:  # noqa: BLE001
            print(f"  [feed err] {source}: {type(e).__name__}: {e}")
            per_feed.append((source, 0))
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
                "source": source,
                "feed_date": _entry_date(entry),
            }
            added += 1
        per_feed.append((source, added))
    ok = sum(1 for _, n in per_feed if n > 0)
    print(f"Feeds parsed: {ok}/{len(FEEDS)} non-empty · "
          f"{len(seen)} unique article URLs after dedupe")
    empties = [s for s, n in per_feed if n == 0]
    if empties:
        print(f"  empty/failed feeds ({len(empties)}): {', '.join(empties)}")
    return list(seen.values())


async def fetch_via_serp(session: aiohttp.ClientSession, url: str, token: str,
                         timeout: float) -> tuple[str, str | None]:
    """POST to serp-api /v1/content → (text, publishedTime). ('','') on failure."""
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
                                timeout: float) -> str:
    """Fallback: raw GET + trafilatura main-text extraction."""
    try:
        async with session.get(
            url, headers={"User-Agent": UA, "Accept": "text/html"},
            timeout=aiohttp.ClientTimeout(total=timeout), allow_redirects=True,
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


async def build(out_path: Path, max_articles: int, concurrency: int,
                time_budget: float, fetch_timeout: float) -> None:
    t0 = time.monotonic()
    token = _serp_token()
    if not token:
        print("WARNING: no serp-api API_TOKEN found — using trafilatura only.")

    items = collect_feed_items()
    if len(items) > max_articles:
        # Round-robin across sources so a 300-entry feed can't crowd out the mix.
        by_src: dict[str, list[dict]] = {}
        for it in items:
            by_src.setdefault(it["source"], []).append(it)
        ordered: list[dict] = []
        queues = list(by_src.values())
        i = 0
        while queues and len(ordered) < max_articles:
            q = queues[i % len(queues)]
            ordered.append(q.pop(0))
            if not q:
                queues.remove(q)
            else:
                i += 1
        items = ordered
        print(f"Capped to {len(items)} articles (round-robin across sources).")

    print(f"Fetching {len(items)} articles via serp-api "
          f"(concurrency={concurrency}, budget={time_budget:.0f}s)…")

    sem = asyncio.Semaphore(concurrency)
    deadline = t0 + time_budget
    records: list[dict] = []
    stats = {"serp": 0, "trafi": 0, "short": 0, "fail": 0, "skipped": 0}
    done = 0

    connector = aiohttp.TCPConnector(limit=concurrency + 4, ssl=False)
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
            if done % 100 == 0:
                print(f"  fetched {done}/{len(items)} "
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
            return {
                "url": item["url"],
                "title": _sanitize(item["title"]),
                "text": _sanitize(text),
                "published_at": published or "",
                "source": item["source"],
            }

        results = await asyncio.gather(*[one(it) for it in items])
    records = [r for r in results if r]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    chars = sum(len(r["text"]) for r in records)
    dated = [r["published_at"][:10] for r in records if r["published_at"]]
    dmin = min(dated) if dated else "?"
    dmax = max(dated) if dated else "?"
    elapsed = time.monotonic() - t0
    print(f"\n=== Rolling corpus built in {elapsed:.1f}s ===")
    print(f"  usable articles: {len(records)}")
    print(f"  extraction: serp-api={stats['serp']} trafilatura={stats['trafi']} "
          f"| dropped: short<{MIN_CHARS}={stats['short']} "
          f"empty/fail={stats['fail']} over-budget-skipped={stats['skipped']}")
    print(f"  date range (published_at): {dmin} .. {dmax}")
    print(f"  total text: {chars:,} chars (~{chars // 4:,} tokens est)")
    print(f"  distinct sources represented: "
          f"{len({r['source'] for r in records})}")
    print(f"  wrote {out_path}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path, default=OUT_DEFAULT)
    p.add_argument("--max-articles", type=int, default=3000)
    p.add_argument("--concurrency", type=int, default=10)
    p.add_argument("--time-budget", type=float, default=600.0,
                   help="hard wall-clock cap on fetching, seconds (~10 min)")
    p.add_argument("--fetch-timeout", type=float, default=45.0)
    args = p.parse_args()
    asyncio.run(build(args.out, args.max_articles, args.concurrency,
                      args.time_budget, args.fetch_timeout))
