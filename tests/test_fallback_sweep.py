import json
import sys

from scripts import fallback_sweep


def test_coverage_reads_only_the_question_and_our_results():
    search = {
        "results": [
            {"url": "https://a.org", "title": "Grammy album record holds", "text": "wins"},
            {
                "url": "https://huggingface.co/datasets/x",
                "title": "Grammy album record holder wins year",
                "text": "",
            },
        ]
    }
    value = fallback_sweep.coverage(
        "Who holds the Grammy album record for wins?", search
    )
    assert value == 1.0
    assert fallback_sweep.coverage("Grammy album record holder", {"results": []}) == 0.0
    mirror_only = {"results": search["results"][1:]}
    assert fallback_sweep.coverage("Grammy album record", mirror_only) == 0.0


def test_signals_read_router_scores_when_kept():
    search = {
        "results": [
            {"url": "https://a.org", "title": "", "text": "", "scores": {"rerank": 0.2, "fused": 0.5}, "highlight_scores": [0.3]},
            {"url": "https://b.org", "title": "", "text": "", "scores": {"rerank": 0.9, "fused": None}},
        ],
        "candidates": {"found": 9},
    }
    out = fallback_sweep.signals("alpha beta", search)
    assert out["top_rerank"] == 0.9 and out["top_fused"] == 0.5
    assert out["top_highlight"] == 0.3 and round(out["log_found"], 3) == 2.303
    assert set(fallback_sweep.signals("alpha", {"results": [{"url": "x"}]})) == {"term_coverage"}


def test_cause_follows_the_misses_buckets():
    base = {"page_hit": False, "snip_hit": False, "answering": {"u": ["exa"]}}
    assert fallback_sweep.cause({**base, "page_hit": True}) == "excerpt"
    assert fallback_sweep.cause({**base, "page_hit": True, "snip_hit": True}) == "hit"
    assert fallback_sweep.cause({**base, "answering": {}}) == "nobody answers"
    assert fallback_sweep.cause({**base, "held": {"u": False}}) == "coverage"
    assert fallback_sweep.cause({**base, "held": {"u": None}}) == "unknown"
    assert fallback_sweep.cause({**base, "held": {"u": True}, "rank": 40}) == "in index, below 10"
    assert fallback_sweep.cause({**base, "held": {"u": True}, "rank": 4}) == "judged miss in top 10"
    assert fallback_sweep.cause({**base, "held": {"u": "not ours"}}) == "not ours to hold"
    assert fallback_sweep.cause({**base, "held": {"u": "not ours", "v": False}}) == "coverage"


def test_merged_context_dedupes_urls_and_reranks():
    ours = {
        "results": [{"url": f"https://www.a.org/{i}", "rank": i + 1} for i in range(7)]
    }
    theirs = {
        "results": [
            {"url": "https://a.org/0", "rank": 1},
            {"url": "https://b.org/x", "rank": 2},
        ]
    }
    rows = fallback_sweep.merged(ours, theirs)["results"]
    assert [r["url"] for r in rows] == [
        *(f"https://www.a.org/{i}" for i in range(5)),
        "https://b.org/x",
    ]
    assert [r["rank"] for r in rows] == list(range(1, 7))
    assert (
        fallback_sweep.merged({"error": "x"}, theirs)["results"][0]["url"]
        == "https://a.org/0"
    )


def judged(qid, hit):
    flag = {"hit": int(hit)}
    return {"question_id": qid, "search_status": "ok", "results": [{"mirror": False, "page": flag, "snippet": flag}]}


def test_report_scores_the_trigger_and_the_fallback(tmp_path, monkeypatch, capsys):
    questions = [{"id": f"q{i}", "question": f"alpha beta gamma {i}"} for i in range(4)]
    qpath = tmp_path / "q.jsonl"
    qpath.write_text("".join(json.dumps(q) + "\n" for q in questions))
    run = tmp_path / "run"
    (run / "answers").mkdir(parents=True)
    texts = ["alpha beta gamma", "alpha beta", "alpha", "nothing"]
    for i, text in enumerate(texts):
        for profile, body in (
            ("desearch-index", {"results": [{"url": "https://a.org", "title": "", "text": text}]}),
            ("octen", {"results": [], "elapsed_seconds": 1.0 + i}),
        ):
            path = run / "searches" / profile / f"q{i}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(body))
    ours_hit = [True, True, False, False]
    base = ["CORRECT", "CORRECT", "INCORRECT", "INCORRECT"]
    merged = ["CORRECT", "INCORRECT", "CORRECT", "CORRECT"]
    ours_eval = tmp_path / "ours.json"
    ours_eval.write_text(json.dumps({"profiles": {"desearch-index": [judged(f"q{i}", h) for i, h in enumerate(ours_hit)]}}))
    their_eval = tmp_path / "theirs.json"
    their_eval.write_text(json.dumps({"profiles": {"octen": [judged(f"q{i}", i == 3) for i in range(4)]}}))

    def rows(profile, values):
        return "".join(json.dumps({"key": f"{profile}/q{i}", "grade": g}) + "\n" for i, g in enumerate(values))

    (run / "answers" / "grades.jsonl").write_text(rows("desearch-index", base))
    (run / "answers" / "fallback_grades.jsonl").write_text(rows("desearch-index+octen", merged))
    misses = tmp_path / "misses.json"
    misses.write_text(json.dumps([
        {"qid": "q0", "page_hit": True, "snip_hit": True, "answering": {}},
        {"qid": "q1", "page_hit": True, "snip_hit": True, "answering": {}},
        {"qid": "q2", "page_hit": True, "snip_hit": False, "answering": {}},
        {"qid": "q3", "page_hit": False, "snip_hit": False, "answering": {"u": ["exa"]}, "held": {"u": False}},
    ]))
    monkeypatch.setattr(sys, "argv", [
        "x", "report", "--questions", str(qpath), "--run", str(run), "--base-eval", str(ours_eval),
        "--fallback-eval", str(their_eval), "--misses", str(misses), "--rates", "0.5", "0.75",
    ])
    fallback_sweep.main()
    out = capsys.readouterr().out
    assert "| term_coverage | 1.00 | 0.667 | 50% | 100% | 100% | 0% | 0% |" in out
    assert "| 0.667 | 50% | 50.0% | 100.0% | +2 / -0 | n/a | 50% (0%) | $0.50 | 3.5s / 4.0s (1.75s) |" in out
    assert "| 1 | 75% | 50.0% | 75.0% | +2 / -1 |" in out
    assert "| 0.667 | 2 | 0 | 1 | 1 | 0 | 0 | 0 | 0 | 0 | +1 / -0 | +1 / -0 |" in out


def test_fixed_cuts_fire_below_each_cut_and_missing_scores_fire_first():
    import math

    rows = [{"signals": {"s": v}} for v in (-math.inf, 1.0, 3.0, 9.0)]
    got = fallback_sweep.fires(rows, "s", [0.5], cuts=[2, 4])
    assert [(cut, len(fired)) for cut, fired in got] == [(2, 2), (4, 3)]


def test_length_fit_matches_the_raw_cut_fire_rate(tmp_path, monkeypatch):
    questions = [{"id": f"q{i}", "question": "x" * (20 + 10 * i)} for i in range(10)]
    qpath = tmp_path / "q.jsonl"
    qpath.write_text("".join(json.dumps(q) + "\n" for q in questions))
    for i in range(10):
        path = tmp_path / "searches" / "desearch-index" / f"q{i}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"results": [{"url": "u", "scores": {"rerank": float(i)}}]}))
    out = tmp_path / "fit.json"
    monkeypatch.setattr(sys, "argv", [
        "x", "fit", "--questions", str(qpath), "--run", "x", "--signals-run", str(tmp_path),
        "--raw-cut", "3", "--out", str(out),
    ])
    fallback_sweep.main()
    fit = json.loads(out.read_text())
    assert fit["slope"] > 0 and fit["fire_rate"] == 0.3
    residual = [i - fallback_sweep.expected(fit, q["question"]) for i, q in enumerate(questions)]
    assert sum(r < fit["cut"] for r in residual) == 3
