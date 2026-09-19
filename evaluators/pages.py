"""Provider-neutral page snapshots and literal highlight provenance checks."""

import asyncio
import hashlib
import ipaddress
import json
import re
import socket
from pathlib import Path

import aiohttp
from bs4 import BeautifulSoup, UnicodeDammit
from yarl import URL

from utils import now, write_json

USER_AGENT = (
    "DesearchSearchEvals/1.0 (https://github.com/Desearch-ai/desearch-search-evals)"
)
DEFAULT_MAX_BYTES = 3 * 1024 * 1024
DEFAULT_MAX_TEXT_CHARS = 150_000
DEFAULT_TIMEOUT = 20
REMOVED_ELEMENTS = ("script", "style", "noscript", "template", "svg")
BLOCK_ELEMENTS = (
    "article",
    "aside",
    "blockquote",
    "br",
    "dd",
    "div",
    "dl",
    "dt",
    "figcaption",
    "figure",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "li",
    "main",
    "nav",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "td",
    "th",
    "tr",
    "ul",
)
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
INTERSTITIAL_TITLES = {
    "just a moment...",
    "access denied",
    "attention required! | cloudflare",
    "verify you are human",
}


class UnsafeURL(ValueError):
    """A requested URL or resolved address is outside the public web."""


def normalize_whitespace(text):
    return re.sub(r"\s+", " ", text).strip()


def _public_address(address):
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    if parsed.version == 6:
        embedded = parsed.ipv4_mapped or parsed.sixtofour
        if embedded:
            return _public_address(str(embedded))
        if parsed.teredo:
            return all(_public_address(str(part)) for part in parsed.teredo)
        if parsed in ipaddress.ip_network("64:ff9b::/96"):
            return _public_address(str(ipaddress.IPv4Address(int(parsed) & 0xFFFFFFFF)))
    return parsed.is_global and not parsed.is_multicast


def canonical_url(url):
    """Validate and canonicalize a public HTTP URL without its fragment."""
    if not isinstance(url, str) or not url or re.search(r"[\s\\\x00-\x1f\x7f]", url):
        raise UnsafeURL("invalid_url")
    try:
        parsed = URL(url)
        host = parsed.host or ""
        port = parsed.port
    except (ValueError, UnicodeError) as error:
        raise UnsafeURL("invalid_url") from error
    if parsed.scheme not in {"http", "https"} or not host:
        raise UnsafeURL("non_http_url")
    if parsed.user is not None or parsed.password is not None:
        raise UnsafeURL("url_credentials")
    if port is None or not 1 <= port <= 65535:
        raise UnsafeURL("invalid_port")
    lowered = host.lower().rstrip(".")
    if (
        lowered == "localhost"
        or lowered.endswith((".localhost", ".local", ".internal"))
        or "%" in host
    ):
        raise UnsafeURL("non_public_host")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if "." not in lowered:
            raise UnsafeURL("non_public_host") from None
    else:
        if not _public_address(str(address)):
            raise UnsafeURL("non_public_address")
    return str(parsed.with_fragment(None))


class PublicResolver(aiohttp.abc.AbstractResolver):
    """Reject non-public DNS answers before the connector can use them."""

    def __init__(self, resolver=None):
        self._resolver = resolver or aiohttp.resolver.ThreadedResolver()

    async def resolve(self, host, port=0, family=socket.AF_INET):
        answers = await self._resolver.resolve(host, port, family)
        if not answers or any(not _public_address(row["host"]) for row in answers):
            raise UnsafeURL("non_public_dns_answer")
        return answers

    async def close(self):
        await self._resolver.close()


def extract_html(html, *, max_text_chars=DEFAULT_MAX_TEXT_CHARS):
    """Extract static body text in DOM order without selecting by question."""
    if max_text_chars < 1:
        raise ValueError("max_text_chars must be positive")
    soup = BeautifulSoup(html, "html.parser")
    title = normalize_whitespace(soup.title.get_text()) if soup.title else ""
    for element in soup.find_all(REMOVED_ELEMENTS):
        element.decompose()
    if soup.head:
        soup.head.decompose()
    for element in list(soup.find_all(attrs={"hidden": True})):
        if element.parent is not None:
            element.decompose()
    for element in list(soup.find_all(attrs={"aria-hidden": "true"})):
        if element.parent is not None:
            element.decompose()
    for element in list(soup.find_all(attrs={"style": True})):
        style = element.get("style", "") if element.parent is not None else ""
        if re.search(
            r"(?:display\s*:\s*none|visibility\s*:\s*hidden)\b", style, re.IGNORECASE
        ):
            element.decompose()
    for element in soup.find_all(BLOCK_ELEMENTS):
        element.insert_before("\n")
        element.insert_after("\n")
    body = soup.body if soup.body else soup
    full_text = normalize_whitespace(body.get_text())
    return {
        "title": title,
        "text": full_text[:max_text_chars],
        "complete": len(full_text) <= max_text_chars,
    }


def cache_path(cache_dir, url):
    key = hashlib.sha256(canonical_url(url).encode()).hexdigest()
    return Path(cache_dir) / f"{key}.json"


async def _read_body(response, max_bytes):
    chunks = []
    size = 0
    async for chunk in response.content.iter_chunked(min(64 * 1024, max_bytes + 1)):
        size += len(chunk)
        if size > max_bytes:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


async def _fetch_page(
    session,
    url,
    *,
    cache_dir,
    timeout,
    max_bytes,
    max_text_chars,
    max_redirects,
    refresh,
):
    policy = {
        "max_bytes": max_bytes,
        "max_text_chars": max_text_chars,
        "max_redirects": max_redirects,
        "timeout_seconds": timeout,
    }
    page = {
        "requested_url": url,
        "final_url": None,
        "retrieved_at": now(),
        "status": "unavailable",
        "error": None,
        "http_status": None,
        "content_type": None,
        "title": "",
        "text": "",
        "complete": False,
        "redirects": [],
        "fetch_policy": policy,
    }
    try:
        current = canonical_url(url)
    except UnsafeURL as error:
        page["error"] = str(error)
        return page
    path = cache_path(cache_dir, current)
    if not refresh and path.exists():
        try:
            cached = json.loads(path.read_text())
            if (
                isinstance(cached, dict)
                and cached.get("fetch_policy") == policy
                and isinstance(cached.get("complete"), bool)
                and canonical_url(cached["requested_url"]) == current
            ):
                return {**cached, "requested_url": url}
        except (ValueError, OSError, KeyError):
            pass

    try:
        async with asyncio.timeout(timeout):
            for redirect_count in range(max_redirects + 1):
                current = canonical_url(current)
                page["final_url"] = current
                async with session.get(
                    current,
                    allow_redirects=False,
                    headers={
                        "User-Agent": USER_AGENT,
                        "Accept": "text/html, application/xhtml+xml",
                    },
                ) as response:
                    page["http_status"] = response.status
                    page["content_type"] = response.headers.get("Content-Type", "")
                    if response.status in REDIRECT_STATUSES:
                        location = response.headers.get("Location")
                        if not location:
                            page["error"] = "redirect_without_location"
                            break
                        if redirect_count == max_redirects:
                            page["error"] = "too_many_redirects"
                            break
                        target = canonical_url(str(URL(current).join(URL(location))))
                        page["redirects"].append(
                            {"from": current, "to": target, "status": response.status}
                        )
                        current = target
                        continue
                    if response.status != 200:
                        page["error"] = f"http_{response.status}"
                        break
                    media_type = page["content_type"].split(";", 1)[0].strip().lower()
                    if media_type not in {"text/html", "application/xhtml+xml"}:
                        page["error"] = "unsupported_content_type"
                        break
                    if response.content_length and response.content_length > max_bytes:
                        page["error"] = "response_too_large"
                        break
                    raw = await _read_body(response, max_bytes)
                    if raw is None:
                        page["error"] = "response_too_large"
                        break
                    decoded = UnicodeDammit(
                        raw,
                        known_definite_encodings=[response.charset]
                        if response.charset
                        else [],
                        is_html=True,
                    )
                    if decoded.unicode_markup is None:
                        page["error"] = "decoding_failed"
                        break
                    page.update(
                        extract_html(
                            decoded.unicode_markup, max_text_chars=max_text_chars
                        )
                    )
                    page["complete"] &= not decoded.contains_replacement_characters
                    if page["title"].lower() in INTERSTITIAL_TITLES:
                        page["error"] = "access_interstitial"
                    elif not page["text"]:
                        page["error"] = "empty_static_body"
                    else:
                        page["status"] = "ok"
                    break
    except UnsafeURL as error:
        page["error"] = str(error)
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError) as error:
        page["error"] = type(error).__name__

    write_json(path, page)
    return page


async def fetch_pages(
    urls,
    *,
    cache_dir,
    concurrency=6,
    timeout=DEFAULT_TIMEOUT,
    max_bytes=DEFAULT_MAX_BYTES,
    max_text_chars=DEFAULT_MAX_TEXT_CHARS,
    max_redirects=5,
    refresh=False,
):
    """Fetch unique public URLs with one shared, bounded static extraction policy."""
    if not 1 <= concurrency <= 32:
        raise ValueError("concurrency must be between 1 and 32")
    if timeout <= 0 or max_bytes < 1 or max_text_chars < 1 or max_redirects < 0:
        raise ValueError("fetch limits must be positive; redirects may be zero")
    urls = list(dict.fromkeys(urls))
    semaphore = asyncio.Semaphore(concurrency)
    resolver = PublicResolver()
    connector = aiohttp.TCPConnector(
        resolver=resolver, limit=concurrency, use_dns_cache=True, ttl_dns_cache=300
    )
    try:
        async with aiohttp.ClientSession(
            connector=connector,
            trust_env=False,
            cookie_jar=aiohttp.DummyCookieJar(),
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as session:

            async def one(url):
                async with semaphore:
                    return url, await _fetch_page(
                        session,
                        url,
                        cache_dir=cache_dir,
                        timeout=timeout,
                        max_bytes=max_bytes,
                        max_text_chars=max_text_chars,
                        max_redirects=max_redirects,
                        refresh=refresh,
                    )

            return dict(await asyncio.gather(*(one(url) for url in urls)))
    finally:
        await resolver.close()
