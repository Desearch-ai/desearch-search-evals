"""Unified provider handlers for the benchmark.

Each provider exposes an async `query(question: str) -> dict` that returns:
  {
    "model": str,
    "answer": str,
    "sources": [{"url", "title", "snippet"}, ...],
    "elapsed_seconds": float,
    "raw": {...optional},
  }
"""

from . import desearch, exa, gpt5mini, perplexity, tavily

__all__ = ["desearch", "exa", "gpt5mini", "perplexity", "tavily"]
