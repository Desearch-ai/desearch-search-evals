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


def test_confidence_takes_the_last_stated_value():
    assert fallback_sweep.confidence("Exact Answer: x\nConfidence: 85%") == 0.85
    assert fallback_sweep.confidence("Confidence: 20%\nConfidence: 60%") == 0.6
    assert fallback_sweep.confidence("") == 0.0


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


def test_sweep_counts_gains_and_losses(tmp_path, monkeypatch, capsys):
    questions = [{"id": f"q{i}", "question": f"alpha beta gamma {i}"} for i in range(4)]
    qpath = tmp_path / "q.jsonl"
    qpath.write_text("".join(json.dumps(q) + "\n" for q in questions))
    run = tmp_path / "run"
    (run / "answers").mkdir(parents=True)
    texts = ["alpha beta gamma", "alpha beta", "alpha", "nothing"]
    for i, text in enumerate(texts):
        path = run / "searches" / "desearch-index" / f"q{i}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"results": [{"url": "https://a.org", "title": "", "text": text}]}
            )
        )
    base = ["CORRECT", "CORRECT", "INCORRECT", "INCORRECT"]
    octen = ["INCORRECT", "INCORRECT", "CORRECT", "INCORRECT"]
    merged = ["CORRECT", "CORRECT", "CORRECT", "CORRECT"]

    def rows(profile, values):
        return [
            {"key": f"{profile}/q{i}", "grade": g, "response": "Confidence: 50%"}
            for i, g in enumerate(values)
        ]

    lines = rows("desearch-index", base) + rows("octen", octen)
    (run / "answers" / "grades.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in lines)
    )
    (run / "answers" / "answers.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in lines)
    )
    (run / "answers" / "fallback_grades.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows("desearch-index+octen", merged))
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["x", "sweep", "--questions", str(qpath), "--run", str(run), "--rates", "0.5"],
    )
    fallback_sweep.main()
    out = capsys.readouterr().out
    assert "desearch-index alone 50.0%; octen alone 25.0%" in out
    half = [line for line in out.splitlines() if line.startswith("| coverage | 0.67")]
    assert (
        half and "| 50% | 75.0% | +1 / -0 | 100.0% | +2 / -0 | 500 ($0.50) |" in half[0]
    )
    everything = [
        line for line in out.splitlines() if line.startswith("| coverage | all")
    ]
    assert "| 100% | 25.0% | +1 / -2 |" in everything[0]
