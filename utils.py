import asyncio
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def load_env(path=None):
    path = Path(path) if path is not None else Path(__file__).parent / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            if key.strip():
                os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line]


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temporary.replace(path)


def digest(value):
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


async def map_batches(items, worker, batch_size):
    if not isinstance(batch_size, int) or isinstance(batch_size, bool):
        raise TypeError("Batch size must be an integer")
    if batch_size < 1:
        raise ValueError("Batch size must be positive")
    items = list(items)
    results = []
    for offset in range(0, len(items), batch_size):
        results.extend(
            await asyncio.gather(
                *(worker(item) for item in items[offset : offset + batch_size])
            )
        )
    return results
