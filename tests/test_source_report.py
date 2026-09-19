from evaluators.source_report import markdown, question_scores, summarize

QUESTIONS = [{"id": "q1"}, {"id": "q2"}]


def result(page_hit=0, snippet_hit=0, rank=1):
    return {
        "rank": rank,
        "mirror": False,
        "page": {"hit": page_hit, "status": "ok", "fetched": True},
        "snippet": {"hit": snippet_hit, "status": "ok"},
    }


def row(question_id, results, status="ok"):
    return {"question_id": question_id, "search_status": status, "results": results}


def test_one_hit_per_question_and_rank_cutoffs():
    scores = question_scores(row("q1", [result(), result(1, 0, 2), result(1, 1, 3)]))
    assert scores["page_hit_at_1"] == 0
    assert scores["page_hit_at_5"] == 1
    assert scores["snippet_hit_at_5"] == 1
    assert scores["page_mrr"] == 0.5
    assert scores["snippet_mrr"] == 1 / 3


def test_failed_missing_and_empty_searches_score_zero():
    graded = {
        "profiles": {"p": [row("q1", [result(1, 1)], status="error"), row("q2", [])]}
    }
    profile = summarize(QUESTIONS, graded, ["p"])["profiles"]["p"]
    assert profile["metrics"]["page_hit_at_10"] == 0
    assert profile["failed_searches"] == 1
    assert profile["empty_responses"] == 1
    missing = summarize(QUESTIONS, {"profiles": {}}, ["p"])["profiles"]["p"]
    assert missing["missing_searches"] == 2
    assert missing["metrics"]["page_hit_at_10"] == 0


def test_mirrors_never_hit():
    mirror = {"rank": 1, "mirror": True, "page": None, "snippet": None}
    assert question_scores(row("q1", [mirror]))["page_hit_at_1"] == 0


def test_repeated_questions_are_rejected():
    graded = {"profiles": {"p": [row("q1", []), row("q1", [])]}}
    try:
        summarize(QUESTIONS, graded, ["p"])
    except ValueError:
        return
    raise AssertionError("duplicate rows must fail")


def test_markdown_reports_both_evidence_bases():
    graded = {"profiles": {"p": [row("q1", [result(1, 0)]), row("q2", [result()])]}}
    text = markdown(summarize(QUESTIONS, graded, ["p"]))
    assert "Page states answer" in text
    assert "50.0% / 50.0% / 50.0%" in text
    assert "precision" not in text.lower()


def test_report_states_judge_accuracy_only_when_calibrated():
    graded = {"profiles": {"p": [row("q1", [result(1, 0)]), row("q2", [result()])]}}
    report = summarize(QUESTIONS, graded, ["p"])
    assert "has not been calibrated" in markdown(report)
    report["calibration"] = {
        "labelled": 200,
        "precision_of_hits": 0.95,
        "share_of_rejections_that_answer": 0.02,
        "recall_of_hits": 0.9,
    }
    text = markdown(report)
    assert "95.0% of counted hits" in text and "200 human-labelled" in text


def test_gold_url_hit_and_recall_use_distinct_reference_pages():
    results = [
        dict(result(), gold=None),
        dict(result(), gold="en.wikipedia.org/wiki/A"),
        dict(result(), gold="en.wikipedia.org/wiki/A"),
        dict(result(), gold="b.org/x"),
    ]
    scores = question_scores(
        {"search_status": "ok", "gold_urls": 3, "results": results}
    )
    assert scores["gold_hit_at_1"] == 0 and scores["gold_hit_at_5"] == 1
    assert scores["gold_recall_at_10"] == 2 / 3
    assert scores["gold_all_at_10"] == 0
    assert "gold_hit_at_1" not in question_scores(row("q1", [result()]))
