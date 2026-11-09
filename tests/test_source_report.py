import pytest


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


def test_rank_header_names_every_cutoff():
    graded = {"profiles": {"p": [row("q1", [result(1, 0)]), row("q2", [result()])]}}
    text = markdown(summarize(QUESTIONS, graded, ["p"]))
    assert "Page states answer @1 / @3 / @5 / @10" in text
    assert "@1 / @5 / @10" not in text


def test_operations_cover_fetching_latency_size_and_cost():
    fallback = dict(result(1, 0), returned_chars=100)
    fallback["page"] = {**fallback["page"], "fetched": False, "evidence_source": "returned_text"}
    fetched = dict(result(1, 0), returned_chars=300)
    fetched["page"]["evidence_source"] = "fetched_page"
    unfetched = dict(result(), returned_chars=200)
    unfetched["page"]["fetched"] = False
    rows = [
        {**row("q1", [fallback, unfetched]), "elapsed_seconds": 1.0, "cost_usd": 0.01},
        {**row("q2", [fetched]), "elapsed_seconds": 3.0, "cost_usd": None},
    ]
    profile = summarize(QUESTIONS, {"profiles": {"p": rows}}, ["p"])["profiles"]["p"]
    assert profile["fetch_rate"] == 1 / 3
    assert profile["page_hits_from_returned_text"] == 0.5
    assert profile["latency_p50_seconds"] == 2.0
    assert profile["latency_p90_seconds"] == pytest.approx(2.8)
    assert profile["mean_returned_chars"] == 200
    assert (profile["cost_per_search_usd"], profile["searches_with_cost"]) == (0.01, 1)
    text = markdown(summarize(QUESTIONS, {"profiles": {"p": rows}}, ["p"]))
    assert "2.00 s / 2.80 s" in text and "$0.0100" in text


def test_latency_and_cost_are_blank_when_absent():
    graded = {"profiles": {"p": [row("q1", [result()])]}}
    profile = summarize(QUESTIONS, graded, ["p"])["profiles"]["p"]
    assert profile["latency_p50_seconds"] is None
    assert profile["cost_per_search_usd"] is None


def test_bootstrap_interval_is_fixed_and_brackets_the_mean():
    questions = [{"id": f"q{i}"} for i in range(40)]
    rows = [row(f"q{i}", [result(int(i % 4 == 0), int(i % 2 == 0))]) for i in range(40)]
    first = summarize(questions, {"profiles": {"p": rows}}, ["p"])["profiles"]["p"]
    again = summarize(questions, {"profiles": {"p": rows}}, ["p"])["profiles"]["p"]
    low, high = first["ci95"]["snippet_hit_at_10"]
    assert first["ci95"] == again["ci95"]
    assert low < 0.5 < high and 0 <= low and high <= 1
    page_low, page_high = first["ci95"]["page_hit_at_10"]
    assert page_low < 0.25 < page_high


def test_paired_table_against_desearch_index():
    graded = {
        "profiles": {
            "desearch-index": [row("q1", [result(1, 1)]), row("q2", [result()])],
            "exa": [row("q1", [result()]), row("q2", [result(1, 0)])],
        }
    }
    report = summarize(QUESTIONS, graded, ["desearch-index", "exa"])
    counts = report["paired"]["profiles"]["exa"]
    assert counts["page"] == {"baseline_only": 1, "profile_only": 1, "both": 0, "neither": 0}
    assert counts["snippet"]["baseline_only"] == 1 and counts["snippet"]["profile_only"] == 0
    assert "Paired against desearch-index" in markdown(report)
    assert summarize(QUESTIONS, graded, ["exa"])["paired"] is None


def test_freshness_split_only_when_questions_carry_it():
    graded = {"profiles": {"p": [row("q1", [result(1, 1)]), row("q2", [result()])]}}
    assert summarize(QUESTIONS, graded, ["p"])["by_freshness"] is None
    questions = [
        {"id": "q1", "freshness": "never-changing"},
        {"id": "q2", "freshness": "fast-changing"},
    ]
    report = summarize(questions, graded, ["p"])
    split = report["by_freshness"]
    assert split["never-changing"]["profiles"]["p"]["page_hit_at_10"] == 1
    assert split["fast-changing"]["profiles"]["p"]["page_hit_at_10"] == 0
    assert split["fast-changing"]["questions"] == 1
    assert "## By freshness @10" in markdown(report)
    assert split["fast-changing"]["profiles"]["p"]["gold_hit_at_10"] is None
