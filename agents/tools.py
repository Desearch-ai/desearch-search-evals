"""The two tools an agent gets: search through the provider under test, and page fetch."""

import json
import os
from pathlib import Path

from evaluators import pages
from providers import desearch_index
from providers import search as search_provider
from utils import digest, write_json

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web and return ranked results with an excerpt from each page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query."}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Fetch one URL that web_search returned and read its text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "A URL from a search result.",
                    }
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "Submit the final answer and end the task.",
            "parameters": {
                "type": "object",
                "properties": {
                    "answer": {
                        "type": "string",
                        "description": "The answer alone, with no explanation. For a list, separate items with semicolons.",
                    }
                },
                "required": ["answer"],
            },
        },
    },
]

MIRROR = (
    "simple-evals",
    "simpleqa",
    "browsecomp",
    "deepsearchqa",
    "huggingface.co/datasets",
)
RESULT_CHARS = 1200
PAGE_CHARS = 6000


def contaminated(url: str, title: str = "") -> bool:
    """Benchmark copies are dropped before the model sees them, as Artificial Analysis does."""
    text = f"{url} {title}".lower()
    return any(marker in text for marker in MIRROR)


class Tools:
    """Search and fetch for one run, cached on disk so a rerun costs nothing."""

    def __init__(
        self, session, profile, cache_dir: Path, page_cache: Path, results: int = 10
    ):
        self.session = session
        self.profile = profile
        self.cache_dir = Path(cache_dir)
        self.page_cache = Path(page_cache)
        self.results = results
        self.searches, self.fetches, self.cost_usd = 0, 0, 0.0
        self.search_errors = 0
        self.queries: list[str] = []
        # Provider latency as measured on the original call, so a cached rerun reports it too.
        self.search_seconds = 0.0

    async def web_search(self, query: str) -> str:
        query = (query or "").strip()
        if not query:
            return "web_search needs a query."

        path = (
            self.cache_dir
            / f"{digest({'profile': self.profile['id'], 'query': query})}.json"
        )
        if path.exists():
            found = json.loads(path.read_text())
        else:
            try:
                found = await search_provider.search(self.session, query, self.profile)
            except Exception:
                # Never cached, and never shown to the model as an empty result.
                self.search_errors += 1
                return "Search failed. Try again or rephrase."
            write_json(path, found)

        self.searches += 1
        self.queries.append(query)
        if isinstance(found.get("elapsed_seconds"), (int, float)):
            self.search_seconds += found["elapsed_seconds"]
        if isinstance(found.get("cost_usd"), (int, float)):
            self.cost_usd += found["cost_usd"]

        rows = [
            row
            for row in found.get("results", [])
            if row.get("url") and not contaminated(row["url"], row.get("title", ""))
        ][: self.results]
        if not rows:
            return "No results."
        return json.dumps(
            [
                {
                    "url": row["url"],
                    "title": row.get("title", ""),
                    "published": row.get("published", ""),
                    "excerpt": (row.get("text") or "")[:RESULT_CHARS],
                }
                for row in rows
            ],
            ensure_ascii=False,
        )

    async def web_fetch(self, url: str) -> str:
        if not url or contaminated(url):
            return "That page cannot be fetched."

        if self.profile.get("fetch") == "corpus":
            # Fixed-corpus benchmarks read pages from the index, never the live web.
            text = await desearch_index.document(
                self.session,
                url,
                self.profile,
                os.environ.get("DESEARCH_INDEX_KEY", ""),
            )
            self.fetches += 1
            if not text:
                return f"Could not read {url}."
            return text[:PAGE_CHARS]

        fetched = await pages.fetch_pages(
            [url], cache_dir=self.page_cache, concurrency=1
        )
        page = fetched.get(url) or {}
        self.fetches += 1
        if not page.get("text"):
            return f"Could not read {url}."
        return page["text"][:PAGE_CHARS]
