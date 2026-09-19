from scripts import prepare_news

ARTICLES = [
    {
        "event_id": "e1",
        "domain": "a.com",
        "url": "https://a.com/1",
        "published": "2026-09-10",
        "title": "A",
        "text": "Homebrew led the $3 million seed round in Nelo.",
    },
    {
        "event_id": "e1",
        "domain": "b.com",
        "url": "https://b.com/1",
        "published": "2026-09-10",
        "title": "B",
        "text": "Nelo raised $3 million in a round led by Homebrew.",
    },
    {
        "event_id": "e1",
        "domain": "b.com",
        "url": "https://b.com/2",
        "published": "2026-09-11",
        "title": "B2",
        "text": "Short follow-up.",
    },
    {
        "event_id": "e2",
        "domain": "a.com",
        "url": "https://a.com/2",
        "published": "2026-09-12",
        "title": "Solo",
        "text": "Only one outlet.",
    },
]
GOOD = {
    "question": "Who led Nelo's $3 million seed round in September 2026?",
    "answer": "Homebrew",
    "event_date": "2026-09-10",
}


def test_one_longest_article_per_publisher():
    picked = prepare_news.outlets(prepare_news.events(ARTICLES)["e1"])
    assert [a["url"] for a in picked] == ["https://b.com/1", "https://a.com/1"]


def test_only_multi_outlet_events_are_sampled():
    grouped = prepare_news.events(ARTICLES)
    assert prepare_news.sample_events(grouped, 10, seed=1) == ["e1"]


def test_answer_must_be_stated_by_two_outlets():
    assert prepare_news.check(GOOD, ARTICLES[:2], "2026-09-09", "2026-09-15") == []
    solo = prepare_news.check(GOOD, ARTICLES[:1], "2026-09-09", "2026-09-15")
    assert "fewer_than_two_outlets_state_answer" in solo


def test_leakage_relative_dates_and_window_are_rejected():
    leaked = {**GOOD, "question": "Did Homebrew lead Nelo's round this week?"}
    problems = prepare_news.check(leaked, ARTICLES[:2], "2026-09-09", "2026-09-15")
    assert {"answer_in_question", "relative_or_meta_wording"} <= set(problems)
    late = {**GOOD, "event_date": "2026-08-01"}
    assert "event_outside_window" in prepare_news.check(
        late, ARTICLES[:2], "2026-09-09", "2026-09-15"
    )


def test_oversized_clusters_are_not_sampled():
    blob = [
        {
            **ARTICLES[0],
            "event_id": "big",
            "url": f"https://a.com/{i}",
            "domain": f"d{i % 3}.com",
        }
        for i in range(40)
    ]
    grouped = prepare_news.events(blob)
    assert prepare_news.sample_events(grouped, 10, seed=1, max_articles=30) == []


def test_sister_sites_of_one_owner_do_not_confirm_each_other():
    sisters = [{**article, "owner": "reach"} for article in ARTICLES[:2]]
    problems = prepare_news.check(GOOD, sisters, "2026-09-09", "2026-09-15")
    assert "fewer_than_two_outlets_state_answer" in problems
