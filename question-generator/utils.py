"""Small shared helpers: .env loading and the dataset-leakage guard."""

from __future__ import annotations

import os
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_LOADED = False


def load_env(path: Path | None = None) -> None:
    """Populate os.environ from a .env file (idempotent, never overrides existing).

    Simple KEY=value lines; surrounding quotes are stripped, # lines ignored."""
    global _LOADED
    if _LOADED:
        return
    env = path or (_HERE / ".env")
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    _LOADED = True


# Benchmark/dataset pages we must never mine questions from: a hit on one of
# these means we'd be inverting an existing answer key, not a primary source.
CONTAMINATION_PATTERNS = {
    "simpleqa", "simple_qa", "simple-qa",
    "frames-benchmark", "frames_benchmark",
    "browsecomp", "browse_comp", "browse-comp",
    "deepsearchqa", "deep_search_qa", "deep-search-qa", "dsqa",
    "seal-0", "seal_0", "sealhard", "seal-hard", "seal_hard",
    "huggingface.co/datasets",
}


def is_contaminated(url: str, title: str = "", snippet: str = "") -> bool:
    """True if this URL/title/text points at a known benchmark dataset page."""
    blob = f"{url} {title} {snippet}".lower()
    return any(p in blob for p in CONTAMINATION_PATTERNS)
