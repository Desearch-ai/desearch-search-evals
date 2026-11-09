import json
import sys

from scripts import answer_report, prepare_frames, run_answers

HEADER = [
    "",
    "Prompt",
    "Answer",
    "wikipedia_link_1",
    "wikipedia_link_2",
    "wikipedia_link_11+",
    "reasoning_types",
    "wiki_links",
]


def write_tsv(path, rows):
    lines = ["\t".join(HEADER)] + ["\t".join(row) for row in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_frames_rows_keep_wording_and_clean_reference_urls(tmp_path):
    source = tmp_path / "test.tsv"
    write_tsv(
        source,
        [
            [
                "0",
                "Which city? ",
                "Lincoln",
                "https://en.wikipedia.org/wiki/Lincoln,_Nebraska",
                "en.wikipedia.org/wiki/Nebraska#History",
                "https://en.wikipedia.org/wiki/A, https://en.wikipedia.org/wiki/B (HELPFUL)",
                "Multiple constraints | Temporal reasoning",
                "['https://en.wikipedia.org/wiki/Lincoln,_Nebraska']",
            ]
        ],
    )
    [row] = prepare_frames.load(source, "abc")
    assert row["id"] == "frames-0"
    assert row["question"] == "Which city? "
    assert row["reference_urls"] == [
        "https://en.wikipedia.org/wiki/Lincoln,_Nebraska",
        "https://en.wikipedia.org/wiki/Nebraska",
        "https://en.wikipedia.org/wiki/A",
        "https://en.wikipedia.org/wiki/B",
    ]
    assert row["reasoning_types"] == ["Multiple constraints", "Temporal reasoning"]
    assert row["dataset_revision"] == "google/frames-benchmark@abc"


def test_frames_sample_is_seeded_and_keeps_dataset_order():
    records = [{"id": f"frames-{i}"} for i in range(50)]
    first = prepare_frames.sample(records, 10, "42")
    assert first == prepare_frames.sample(records, 10, "42")
    assert first != prepare_frames.sample(records, 10, "7")
    assert [r["id"] for r in first] == sorted(
        (r["id"] for r in first), key=lambda i: int(i.split("-")[1])
    )


def test_frames_grader_uses_the_paper_prompt_and_parses_decisions():
    body = run_answers.grader_request("frames", "Q?", "Jane Ballou", "It is Jane.")
    assert body["model"] == run_answers.GRADERS["frames"]["model"]
    assert body["messages"][0] == {
        "role": "system",
        "content": "You are a helpful assistant.",
    }
    prompt = body["messages"][1]["content"]
    assert "- Ground Truth Answer: Jane Ballou" in prompt
    assert "- Predicted Answer: It is Jane." in prompt
    assert "{" not in prompt
    assert body["max_tokens"] > 5
    parse = run_answers.GRADERS["frames"]["parse"]
    assert parse("Explanation: matches.\nDecision: TRUE") == "CORRECT"
    assert parse('"Decision:" "FALSE"') == "INCORRECT"
    assert parse("Decision TRUE then Decision: FALSE") == "INCORRECT"
    assert parse("no verdict") == "UNPARSED"


def test_letter_graders_keep_their_short_requests():
    body = run_answers.grader_request("sealqa", "Q?", "T", "R")
    assert body["max_tokens"] == 5
    assert [m["role"] for m in body["messages"]] == ["user"]
    assert run_answers.GRADERS["sealqa"].get("parse", run_answers.letter_grade)(
        "C"
    ) == ("NOT_ATTEMPTED")


def test_answer_report_splits_frames_by_reasoning_type(tmp_path, monkeypatch, capsys):
    questions = [
        {"id": "f1", "reasoning_types": ["Numerical reasoning"]},
        {"id": "f2", "reasoning_types": ["Numerical reasoning", "Tabular reasoning"]},
    ]
    qpath = tmp_path / "q.jsonl"
    qpath.write_text("".join(json.dumps(q) + "\n" for q in questions))
    config = tmp_path / "c.json"
    config.write_text(json.dumps({"profiles": [{"id": "desearch-index"}]}))
    answers = tmp_path / "run" / "answers"
    answers.mkdir(parents=True)
    grades = [
        ("desearch-index/f1", "CORRECT"),
        ("desearch-index/f2", "INCORRECT"),
        ("no-search/f1", "INCORRECT"),
        ("no-search/f2", "INCORRECT"),
    ]
    (answers / "grades.jsonl").write_text(
        "".join(json.dumps({"key": k, "grade": g}) + "\n" for k, g in grades)
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "x",
            "--questions",
            str(qpath),
            "--config",
            str(config),
            "--run",
            str(tmp_path / "run"),
        ],
    )
    answer_report.main()
    report = (answers / "report.md").read_text()
    assert "### Reasoning: Numerical reasoning (2 questions)" in report
    assert "### Reasoning: Tabular reasoning (1 questions)" in report
    numerical = report.split("### Reasoning: Numerical reasoning")[1].split("###")[0]
    assert "| desearch-index | 50.0%" in numerical
    assert "Freshness" not in report
