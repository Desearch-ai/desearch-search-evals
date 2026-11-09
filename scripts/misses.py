"""Sort our misses into coverage, ranking and excerpt misses, using the pages other providers answered from."""

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from functools import lru_cache

OURS = "desearch-index"
NOT_OURS = "not ours"
SAFE = "()_,!:-.~/"
USER_AGENT = "desearch-search-evals misses lookup (https://github.com/Desearch-ai/desearch-search-evals)"
ARCHIVE = re.compile(r"^(?:https?://)?web\.archive\.org/web/[^/]+/(.+)$", re.I)
SCHEME = re.compile(r"^(https?):/+", re.I)
REST = (
    re.compile(r"^/api/rest_v1/page/[a-z-]+/([^/?]+)"),
    re.compile(r"^/w/rest\.php/v1/page/([^/?]+)"),
)


def unwrap_archive(url):
    found = ARCHIVE.match(url)
    if not found:
        return url
    inner = SCHEME.sub(lambda m: m.group(1).lower() + "://", found.group(1))
    return inner if "://" in inner else "https://" + inner


def wiki_title(url):
    """(desktop host, title with underscores) for a Wikipedia article URL in any of its API or mobile forms."""
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    if not host.endswith(".wikipedia.org"):
        return None
    host = host.replace(".m.wikipedia.org", ".wikipedia.org")
    query = urllib.parse.parse_qs(parts.query)

    def first(*names):
        return next((query[n][0] for n in names if query.get(n)), None)

    title = None
    if parts.path.startswith("/wiki/"):
        title = urllib.parse.unquote(parts.path[len("/wiki/") :])
    elif parts.path == "/w/index.php":
        title = first("title")
    elif parts.path == "/w/api.php":
        title = (first("page", "titles") or "").split("|")[0]
    else:
        for pattern in REST:
            found = pattern.match(parts.path)
            if found:
                title = urllib.parse.unquote(found.group(1))
                break
    title = (title or "").strip().replace(" ", "_")
    return (host, title) if title else None


def wiki_url(host, title):
    return (
        f"https://{host}/wiki/{urllib.parse.quote(title.replace(' ', '_'), safe=SAFE)}"
    )


def forms(url):
    """Base forms in lookup order: fragment stripped, archive unwrapped, Wikipedia title canonical."""
    stripped = (url or "").strip().split("#", 1)[0]
    unwrapped = unwrap_archive(stripped)
    out = [unwrapped]
    wiki = wiki_title(unwrapped)
    if wiki:
        out.append(wiki_url(*wiki))
    return list(dict.fromkeys(out)), wiki


def variants(url):
    """https, host and www.host, with and without a trailing slash."""
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    other = host.removeprefix("www.") if host.startswith("www.") else "www." + host
    path = parts.path or "/"
    if path == "/":
        paths = ["/", ""]
    elif path.endswith("/"):
        paths = [path, path.rstrip("/")]
    else:
        paths = [path, path + "/"]
    query = "?" + parts.query if parts.query else ""
    out = [url] if parts.scheme == "http" else []
    out += [f"https://{h}{p}{query}" for h in (host, other) for p in paths]
    return list(dict.fromkeys(out))


def is_held(info):
    return bool(
        info.get("found")
        and (info.get("page") or {}).get("live")
        and info.get("vectors")
    )


def lookup(url, page, resolve):
    """(status, matched URL): True when a form is found, live and has vectors; NOT_OURS when a redirect lands on a disambiguation page."""
    bases, wiki = forms(url)
    failed = False

    def attempt(base):
        nonlocal failed
        for candidate in variants(base):
            info = page(candidate)
            if info is None:
                failed = True
            elif is_held(info):
                return candidate
        return None

    for base in bases:
        hit = attempt(base)
        if hit:
            return True, hit
    if wiki:
        target = resolve(*wiki)
        if target is None:
            failed = True
        else:
            title, redirected, disambiguation = target
            if title != wiki[1]:
                hit = attempt(wiki_url(wiki[0], title))
                if hit:
                    return True, hit
            if redirected and disambiguation:
                return NOT_OURS, None
    return (None if failed else False), None


def fetch_json(url, body=None, tries=4, wait=20):
    headers = {"user-agent": USER_AGENT}
    if body is not None:
        headers["content-type"] = "application/json"
    data = None if body is None else json.dumps(body).encode()
    for _ in range(tries):
        try:
            request = urllib.request.Request(url, data, headers)
            return json.load(urllib.request.urlopen(request, timeout=90))
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return {"found": False}
            time.sleep(wait)
        except (urllib.error.URLError, TimeoutError, ValueError):
            time.sleep(wait)
    return None


def wiki_resolver(fetch=fetch_json, pause=0.2):
    @lru_cache(maxsize=None)
    def resolve(host, title):
        params = {
            "action": "query",
            "redirects": 1,
            "titles": title.replace("_", " "),
            "prop": "pageprops",
            "ppprop": "disambiguation",
            "format": "json",
            "formatversion": 2,
        }
        time.sleep(pause)
        data = fetch(f"https://{host}/w/api.php?" + urllib.parse.urlencode(params))
        if data is None:
            return None
        query = data.get("query") or {}
        pages = query.get("pages") or []
        if not pages or pages[0].get("missing") or pages[0].get("invalid"):
            return title, False, False
        props = pages[0].get("pageprops") or {}
        return (
            pages[0]["title"].replace(" ", "_"),
            bool(query.get("redirects")),
            "disambiguation" in props,
        )

    return resolve


def router_page(router):
    @lru_cache(maxsize=None)
    def page(url):
        return fetch_json(f"{router}/page?url=" + urllib.parse.quote(url, safe=""))

    return page


def canon(url):
    """One grouping key per page, so several providers' spellings of it count once."""
    bases, wiki = forms(url)
    if wiki:
        return wiki_url(*wiki)
    parts = urllib.parse.urlsplit(bases[0])
    host = (parts.hostname or "").lower().removeprefix("www.")
    query = "?" + parts.query if parts.query else ""
    return f"https://{host}{parts.path.rstrip('/') or '/'}{query}"


def hits(rows, kind):
    return [
        r for r in rows[:10] if (r.get(kind) or {}).get("hit") and not r.get("mirror")
    ]


def collect(profiles):
    ours = {r["question_id"]: r for r in profiles[OURS]}
    others = {
        p: {r["question_id"]: r for r in rows}
        for p, rows in profiles.items()
        if p != OURS
    }
    cases = []
    for qid, row in ours.items():
        answering, spellings = {}, {}
        for profile, rows in others.items():
            for result in hits((rows.get(qid) or {}).get("results") or [], "page"):
                key = canon(result["url"])
                answering.setdefault(key, []).append(profile)
                spellings.setdefault(key, []).append(result["url"])
        cases.append(
            {
                "qid": qid,
                "page_hit": bool(hits(row["results"], "page")),
                "snip_hit": bool(hits(row["results"], "snippet")),
                "answering": answering,
                "spellings": {k: sorted(set(v)) for k, v in spellings.items()},
            }
        )
    return cases


def classify(case, page, resolve, search):
    held, matched = {}, {}
    for key, urls in case["answering"].items():
        statuses = []
        for url in [key, *urls]:
            status, hit = lookup(url, page, resolve)
            statuses.append(status)
            if status is True:
                matched[key] = hit
                break
        held[key] = next((s for s in (True, NOT_OURS, None) if s in statuses), False)
    case["held"], case["matched"] = held, matched
    if any(v is True for v in held.values()):
        response = search(case["qid"])
        keys = {k for k, v in held.items() if v is True} | {
            canon(u) for u in matched.values()
        }
        ranks = [
            r["rank"]
            for r in (response or {}).get("results", [])
            if canon(r["url"]) in keys
        ]
        case["search_failed"] = (
            None
            if response is None
            else {k: v for k, v in (response.get("failed") or {}).items() if v}
        )
        case["rank"] = min(ranks) if ranks else None
    return case


def main():
    from scripts.fallback_sweep import cause

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run",
        required=True,
        help="run dir with evaluation/results.json and questions.jsonl",
    )
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    router = os.environ["DESEARCH_ROUTER_URL"].rstrip("/")
    profiles = json.load(open(f"{args.run}/evaluation/results.json"))["profiles"]
    questions = {
        json.loads(l)["id"]: json.loads(l)
        for l in open(f"{args.run}/questions.jsonl")
        if l.strip()
    }
    page, resolve = router_page(router), wiki_resolver()

    def search(qid):
        return fetch_json(
            f"{router}/search", {"query": questions[qid]["question"], "k": 100}
        )

    cases = collect(profiles)
    todo = [c for c in cases if not c["page_hit"] and c["answering"]]
    print(
        f"{len(cases)} questions, ours page hit {sum(c['page_hit'] for c in cases)}, others only {len(todo)}",
        flush=True,
    )
    for i, case in enumerate(todo):
        classify(case, page, resolve, search)
        print(
            f"{i + 1}/{len(todo)} {case['qid']} held={sum(v is True for v in case['held'].values())}/{len(case['held'])} rank={case.get('rank')}",
            flush=True,
        )
    json.dump(cases, open(args.out, "w"), indent=1)
    for name, count in Counter(cause(c) for c in cases).most_common():
        print(f"BUCKET {count:4d}  {name}")


if __name__ == "__main__":
    main()
