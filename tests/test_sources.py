"""Preserve ranked source normalization and its validation boundary."""

import copy

import pytest

from evaluators.sources import MAX_SOURCE_CHARS, JudgmentError, normalize_sources


def test_preserves_rank_order_and_caps_only_native_body():
    title = "Long title " * 300
    body = "🌍é" * 1001
    result = {
        "results": [
            {
                "rank": rank,
                "title": title,
                "text": body,
                "url": f"https://example.org/{rank}",
                "published": "2026-09-15",
            }
            for rank in range(1, 12)
        ]
    }
    original = copy.deepcopy(result)
    normalized = normalize_sources(result)
    assert len(normalized) == 10
    assert [row["rank"] for row in normalized] == list(range(1, 11))
    assert [row["source_id"] for row in normalized] == [
        f"s{rank}" for rank in range(1, 11)
    ]
    assert normalized[0] == {
        "source_id": "s1",
        "rank": 1,
        "url": "https://example.org/1",
        "title": title,
        "published": "2026-09-15",
        "text": body[:MAX_SOURCE_CHARS],
        "original_text_chars": 2002,
        "truncated": True,
    }
    assert result == original


def test_retains_empty_source_slots_without_backfill():
    result = {"results": [{}, {"text": None}, {"text": "Answer."}]}
    normalized = normalize_sources(result)
    assert [row["text"] for row in normalized] == ["", "", "Answer."]
    assert [row["rank"] for row in normalized] == [1, 2, 3]
    assert normalized[0]["original_text_chars"] == 0
    assert normalized[0]["truncated"] is False
    assert normalize_sources({}) == []


@pytest.mark.parametrize(
    "result, message",
    [
        ({"results": {}}, "ranked list"),
        ({"results": ["source"]}, "not an object"),
        ({"results": [{"text": 123}]}, "text is not a string"),
        ({"results": [{"rank": 2, "text": "Answer."}]}, "ranks disagree"),
    ],
)
def test_rejects_invalid_ranked_sources(result, message):
    with pytest.raises(JudgmentError, match=message):
        normalize_sources(result)
