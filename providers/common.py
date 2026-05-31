"""Shared utilities for provider handlers."""

from __future__ import annotations

import os
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_FALLBACK_ENVS = (_REPO_ROOT / ".env",)
_LOADED = False


def load_env() -> None:
    """Populate os.environ from the repo-root .env (idempotent, never overrides)."""
    global _LOADED
    if _LOADED:
        return
    for env in _FALLBACK_ENVS:
        if not env.exists():
            continue
        for line in env.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    _LOADED = True


def load_key(name: str) -> str:
    """Get an API key from env, with .env fallback (repo root first)."""
    load_env()
    k = os.environ.get(name)
    if k:
        return k
    raise RuntimeError(f"{name} not set in env or .env (looked in {_FALLBACK_ENVS})")
