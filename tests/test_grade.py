import asyncio

from evaluators import grade


def test_long_page_keeps_opening_and_the_passage_that_answers():
    question = {
        "question": "How many children did Gilbert and Mary Hani have?",
        "answer": "6",
    }
    page = "Opening paragraph.\n" * 80 + "Unrelated 6 page footer.\n" * 200
    page += (
        "Chris Hani was the fifth of six children of Gilbert and Mary Hani.\n"
        + "tail\n" * 3000
    )
    text = grade.evidence_text(page, question)
    assert len(text) <= grade.PAGE_CHARS + 20
    assert text.startswith("Opening paragraph.")
    assert "fifth of six children of Gilbert and Mary Hani" in text


def test_long_page_keeps_the_passage_its_snippet_came_from():
    question = {
        "question": "What kind of ship was the Q-ship Salvia made to resemble?",
        "answer": "tramp steamer (or collier)",
    }
    snippet = "Salvia was given a false counter-stern so that she looked like a small 1,000-ton tramp from the sea."
    page = (
        "Project Gutenberg metadata.\n" * 200
        + "Filler text about the war at sea.\n" * 1500
        + snippet
        + "\n"
        + "end\n" * 500
    )
    assert snippet in grade.evidence_text(page, question, snippet)


def test_short_page_is_unchanged():
    assert (
        grade.evidence_text("short page", {"question": "q", "answer": "x"})
        == "short page"
    )


def test_judging_policy_reads_matches_and_top_results_only():
    assert grade.needs_judgment("exact", 9)
    assert grade.needs_judgment("none", grade.JUDGED_RANKS)
    assert not grade.needs_judgment("none", grade.JUDGED_RANKS + 1)


def test_benchmark_mirrors_are_detected():
    assert grade.is_mirror({"url": "https://huggingface.co/datasets/openai/simple_qa"})
    assert not grade.is_mirror(
        {"url": "https://en.wikipedia.org/wiki/Orin", "text": "Orin won."}
    )


class FakeJudge:
    def __init__(self, label, extracted="Orin", quote="Orin won."):
        self.label, self.extracted, self.quote, self.calls = label, extracted, quote, 0

    async def grade(self, question, title, text, published):
        self.calls += 1
        answers = self.label == "answers"
        judgment = {
            "label": self.label,
            "extracted_answer": self.extracted if answers else None,
            "highlights": [
                {
                    "field": "text",
                    "quote": self.quote,
                    "start": 0,
                    "end": len(self.quote),
                }
            ]
            if answers
            else [],
        }
        return "evidence-1", judgment, "ok"


def test_unmatched_low_rank_result_is_not_judged_and_does_not_hit():
    judge = FakeJudge("answers")
    question = {"answer": "Orin"}
    row = asyncio.run(
        grade.grade_basis(judge, question, 7, "Title", "Nothing here.", None, {})
    )
    assert (row["status"], row["hit"], judge.calls) == ("not_judged", 0, 0)


def test_string_match_is_confirmed_by_the_judge():
    question = {"answer": "Orin"}
    evidence = {}
    hit = asyncio.run(
        grade.grade_basis(
            FakeJudge("answers"), question, 8, "T", "Orin won.", None, evidence
        )
    )
    miss = asyncio.run(
        grade.grade_basis(
            FakeJudge("no_answer"), question, 8, "T", "Orin lost.", None, {}
        )
    )
    assert (hit["screen"], hit["hit"], miss["hit"]) == ("exact", 1, 0)
    assert evidence["evidence-1"]["text"] == "Orin won."


def test_empty_result_is_marked_empty():
    row = asyncio.run(
        grade.grade_basis(FakeJudge("answers"), {"answer": "x"}, 1, "", "", None, {})
    )
    assert (row["status"], row["hit"]) == ("empty", 0)


def test_answer_copied_from_the_key_but_absent_from_cited_text_is_rejected():
    judge = FakeJudge("answers", extracted="$749", quote="the $499 introductory price")
    row = asyncio.run(
        grade.grade_basis(
            judge, {"answer": "$749"}, 1, "T", "the $499 introductory price", None, {}
        )
    )
    assert (row["status"], row["hit"]) == ("unsupported_answer", 0)


def test_answer_wording_differences_do_not_trip_the_quote_guard():
    assert grade.quoted("Orin", "Orin won.")
    assert grade.quoted(
        "J.H. Campbell Generating Plant", "the JH Campbell Generating Plant in Michigan"
    )
    assert grade.quoted(
        "the Beijing Xiangshan Forum", "open this week's Beijing Xiangshan Forum"
    )
    assert not grade.quoted("$749", "the $499 introductory price")
    assert not grade.quoted("10", "In 2010 the plant opened")


def test_passages_of_one_long_block_keep_their_own_offsets():
    offsets = [offset for offset, _ in grade.passages_of("x" * 3000, size=1200)]
    assert offsets == [0, 1200, 2400]


def test_benchmark_copies_with_underscored_names_are_mirrors():
    assert grade.is_mirror(
        {"url": "https://huggingface.co/datasets/someone/simpleqa_verified_Urdu"}
    )
    assert grade.is_mirror({"title": "SealQA hard split"})
    assert not grade.is_mirror({"url": "https://example.org/simplest-quarter"})


def test_passage_overlapping_the_opening_keeps_its_later_part():
    question = {
        "question": "How many children did Gilbert and Mary Hani have?",
        "answer": "6",
    }
    page = (
        "x" * 1600
        + " He was the fifth of the six children of Gilbert and Mary Hani. "
        + "y" * 9000
    )
    assert "six children of Gilbert and Mary Hani" in grade.evidence_text(
        page, question
    )


def test_url_key_ignores_spelling_differences_of_one_page():
    assert grade.url_key(
        "https://en.m.wikipedia.org/wiki/Kenichi_Horie/"
    ) == grade.url_key("http://en.wikipedia.org/wiki/Kenichi_Horie")
    assert grade.url_key("https://www.example.org/a%20b") == grade.url_key(
        "https://example.org/a b"
    )
    assert grade.gold_keys(
        {
            "reference_urls": ["https://example.org/x"],
            "urls": ["https://www.example.org/x/"],
        }
    ) == {"example.org/x"}


def test_failed_judge_requests_are_neither_cached_nor_counted_as_verdicts(
    tmp_path, monkeypatch
):
    calls = []

    async def failing_chat(session, key, model, messages, **kwargs):
        calls.append(model)
        return {"error": "request_failed", "attempts": [{"status": 402}]}

    monkeypatch.setattr(grade.judge, "chat", failing_chat)
    judge_ = grade.Judge(None, "key", tmp_path, "model", 2)
    question = {"question": "Who won?", "answer": "Orin"}
    for _ in range(2):
        _, judgment, status = asyncio.run(
            judge_.grade(question, "T", "Orin won.", None)
        )
        assert (judgment, status) == (None, "judge_unavailable")
    assert len(calls) == 2
    assert (
        not list((tmp_path / "calls").glob("*.json"))
        if (tmp_path / "calls").exists()
        else True
    )


def test_gold_only_grading_makes_no_fetches_or_judge_calls(tmp_path, monkeypatch):
    import argparse
    import json

    run = tmp_path / "run"
    (run / "searches" / "p").mkdir(parents=True)
    question = {
        "id": "q1",
        "question": "Q?",
        "answer": "A",
        "reference_urls": ["https://en.wikipedia.org/wiki/A"],
    }
    (run / "questions.jsonl").write_text(json.dumps(question) + "\n")
    (run / "config.json").write_text(json.dumps({"profiles": [{"id": "p"}]}))
    result = {
        "query": "Q?",
        "status": "ok",
        "results": [
            {"url": "https://en.m.wikipedia.org/wiki/A", "title": "A", "text": "x"}
        ],
    }
    (run / "searches" / "p" / "q1.json").write_text(json.dumps(result))

    async def forbidden(*args, **kwargs):
        raise AssertionError("no network in gold-only mode")

    monkeypatch.setattr(grade.pages, "fetch_pages", forbidden)
    monkeypatch.setattr(grade.judge, "chat", forbidden)
    args = argparse.Namespace(
        run=run,
        out=run / "evaluation",
        page_cache=run / "cache",
        model="m",
        concurrency=1,
        fetch_concurrency=1,
        batch_size=5,
        env_file=tmp_path / "none",
        gold_only=True,
    )
    report = asyncio.run(grade.execute(args))
    row = report["profiles"]["p"][0]
    assert (
        row["gold_urls"] == 1 and row["results"][0]["gold"] == "en.wikipedia.org/wiki/A"
    )
    assert row["results"][0]["page"] is None
