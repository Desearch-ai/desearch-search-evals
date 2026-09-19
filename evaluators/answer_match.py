"""Deterministic check for a verified answer inside source text, so the judge is only needed where wording is ambiguous."""

import re
import unicodedata
from datetime import date

SCALES = {
    "hundred": 100,
    "thousand": 10**3,
    "million": 10**6,
    "billion": 10**9,
    "trillion": 10**12,
}
MONTHS = {
    month: index + 1
    for index, month in enumerate(
        "january february march april may june july august september october november december".split()
    )
}
MONTHS.update({name[:3]: number for name, number in list(MONTHS.items())})
NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
DATE_PATTERNS = (
    re.compile(r"\b(\d{4}) (\d{1,2}) (\d{1,2})\b"),
    re.compile(r"\b(\d{1,2})\s+([a-z]+)\s+(\d{4})\b"),
    re.compile(r"\b([a-z]+)\s+(\d{1,2}),?\s+(\d{4})\b"),
)
WORD_NUMBERS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}


def normalize(text: str) -> str:
    """Case, accents, quotes and punctuation removed; percent and currency kept as words."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(
        character for character in text if not unicodedata.combining(character)
    )
    text = (
        text.lower()
        .replace("%", " percent ")
        .replace("$", " dollar ")
        .replace("£", " pound ")
    )
    text = text.replace("€", " euro ")
    text = re.sub(r"[^a-z0-9.,\s]", " ", text)
    text = re.sub(r"(?<!\d)[.,]|[.,](?!\d)", " ", text)
    return " ".join(text.split())


def numbers(text: str) -> set[float]:
    """Numeric values in the text, including scale words and spelled-out small numbers."""
    found = set()
    words = normalize(text).split()
    for index, word in enumerate(words):
        if word in WORD_NUMBERS:
            found.add(float(WORD_NUMBERS[word]))
        for raw in NUMBER.findall(word):
            try:
                value = float(raw.replace(",", ""))
            except ValueError:
                continue
            found.add(value)
            following = words[index + 1] if index + 1 < len(words) else ""
            if following in SCALES:
                found.add(value * SCALES[following])
    return found


def dates(text: str) -> set[date]:
    found = set()
    plain = normalize(text)
    for pattern in DATE_PATTERNS:
        for match in pattern.findall(plain):
            parts = list(match)
            try:
                if parts[0].isdigit() and len(parts[0]) == 4:
                    year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
                elif parts[1] in MONTHS:
                    day, month, year = int(parts[0]), MONTHS[parts[1]], int(parts[2])
                elif parts[0] in MONTHS:
                    month, day, year = MONTHS[parts[0]], int(parts[1]), int(parts[2])
                else:
                    continue
                found.add(date(year, month, day))
            except ValueError:
                continue
    return found


RANGE = re.compile(
    r"\(\s*acceptable range:?\s*(?:anything\s+)?between\s+([\d.,]+)\D+?([\d.,]+)[^)]*\)",
    re.IGNORECASE,
)
ALTERNATIVE = re.compile(r"\(\s*(?:or|aka|also)\s+([^)]+)\)\s*$", re.IGNORECASE)


def forms(answer: str) -> tuple[list[str], tuple[float, float] | None]:
    """Written forms of a reference answer and its accepted numeric range, from SimpleQA-style tolerances."""
    bounds = None
    if match := RANGE.search(answer):
        try:
            bounds = (
                float(match[1].replace(",", "")),
                float(match[2].replace(",", "")),
            )
        except ValueError:
            bounds = None
        answer = RANGE.sub("", answer).strip()
    written = [answer]
    if match := ALTERNATIVE.search(answer):
        written = [ALTERNATIVE.sub("", answer).strip(), match[1].strip().strip("\"'")]
    return [form for form in written if form], bounds


def contains(text: str, answer: str, aliases=()) -> str:
    """One of exact, numeric, date or none: how strongly the text carries the verified answer."""
    written, bounds = forms(answer)
    haystack = f" {normalize(text)} "
    for candidate in (*written, *aliases):
        needle = normalize(candidate)
        if needle and f" {needle} " in haystack:
            return "exact"
    primary = written[0] if written else answer
    answer_dates = dates(primary)
    if answer_dates and answer_dates <= dates(text):
        return "date"
    if bounds and any(bounds[0] <= value <= bounds[1] for value in numbers(text)):
        return "numeric"
    answer_numbers = numbers(primary)
    if answer_numbers and answer_numbers <= numbers(text):
        return "numeric"
    return "none"


def screen(sources, answer, aliases=(), field="text"):
    """Label every source before any judging, so only unclear sources need a model."""
    return [contains(source.get(field) or "", answer, aliases) for source in sources]
