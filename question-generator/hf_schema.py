"""Locked public dataset schema: row format, window derivation, validation.

Shared by the backfill retrofit and the forward/gap generator so both emit an
identical public row: {id, question, difficulty, start_date, end_date}.
"""

from datetime import datetime, timedelta, timezone

FMT = "%Y-%m-%dT%H:%M:%SZ"
DAYS_BEFORE = 2
DAYS_AFTER = 5

PUBLIC_KEYS = {"id", "question", "difficulty", "start_date", "end_date"}
FORBIDDEN_KEYS = {
    "gold_answer",
    "answer_span",
    "source_url",
    "source",
    "qtype",
    "answer_type",
}


def _parse(published_iso):
    return datetime.fromisoformat(published_iso.replace("Z", "+00:00")).astimezone(
        timezone.utc
    )


def source_day(published_iso):
    return _parse(published_iso).strftime("%Y-%m-%d")


def derive_window(published_iso, days_before=DAYS_BEFORE, days_after=DAYS_AFTER):
    """Absolute [start, end] around the source day: [source-days_before, source+days_after]."""
    src = _parse(published_iso).replace(hour=0, minute=0, second=0, microsecond=0)
    start = src - timedelta(days=days_before)
    end = src + timedelta(days=days_after + 1)

    return start.strftime(FMT), end.strftime(FMT)


def build_row(qid, question, difficulty, published_iso):
    start, end = derive_window(published_iso)

    return {
        "id": qid,
        "question": question,
        "difficulty": difficulty,
        "start_date": start,
        "end_date": end,
    }


def validate_row(row, published_iso=None):
    extra = set(row) - PUBLIC_KEYS
    missing = PUBLIC_KEYS - set(row)
    assert not extra and not missing, f"bad keys (extra={extra}, missing={missing})"

    s = datetime.strptime(row["start_date"], FMT).replace(tzinfo=timezone.utc)
    e = datetime.strptime(row["end_date"], FMT).replace(tzinfo=timezone.utc)
    assert s < e, f"start >= end: {row['start_date']} .. {row['end_date']}"

    if published_iso is not None:
        p = _parse(published_iso)
        assert s <= p <= e, (
            f"source {published_iso} not in [{row['start_date']}, {row['end_date']}]"
        )

    return True
