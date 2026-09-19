"""Normalize ranked native sources without changing their evidence or order."""

MAX_SOURCE_CHARS = 2000


class JudgmentError(ValueError):
    """Signal an unusable judge response without converting it to a search failure."""


def normalize_sources(result: dict) -> list[dict]:
    rows = result.get("results", [])
    if not isinstance(rows, list):
        raise JudgmentError("Provider results must be a ranked list")

    sources = []
    for index, row in enumerate(rows[:10], 1):
        if not isinstance(row, dict):
            raise JudgmentError(f"Provider result {index} is not an object")
        text = row.get("text") or ""
        if not isinstance(text, str):
            raise JudgmentError(f"Provider result {index} text is not a string")
        if "rank" in row and row["rank"] != index:
            raise JudgmentError("Provider result ranks disagree with list order")
        sources.append(
            {
                "source_id": f"s{index}",
                "rank": index,
                "url": row.get("url", ""),
                "title": row.get("title", ""),
                "published": row.get("published"),
                "text": text[:MAX_SOURCE_CHARS],
                "original_text_chars": len(text),
                "truncated": len(text) > MAX_SOURCE_CHARS,
            }
        )
    return sources
