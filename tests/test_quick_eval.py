import json

import pytest

from scripts import quick_eval
from scripts.run_answers import grades_name

LOCK = "# lock\n\nrules\n\n## Holder\n(free)\n"


def test_lock_is_taken_then_released(tmp_path):
    path = tmp_path / "lock.md"
    path.write_text(LOCK)
    quick_eval.acquire(path, "- me | run | 10:00")
    assert quick_eval.holders(path.read_text()) == ["- me | run | 10:00"]
    with pytest.raises(SystemExit, match="locked by: - me"):
        quick_eval.acquire(path, "- other | run | 10:01")
    quick_eval.acquire(path, "- other | run | 10:01", share=True)
    quick_eval.release(path, "- me | run | 10:00")
    assert quick_eval.holders(path.read_text()) == ["- other | run | 10:01"]
    quick_eval.release(path, "- other | run | 10:01")
    assert path.read_text() == LOCK


def test_subset_keeps_listed_ids_and_rejects_unknown(tmp_path):
    questions = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    ids = tmp_path / "ids.txt"
    ids.write_text("c\n# note\na\n")
    assert [q["id"] for q in quick_eval.subset(questions, ids)] == ["a", "c"]
    ids.write_text("z\n")
    with pytest.raises(SystemExit):
        quick_eval.subset(questions, ids)


def write_search(run, profile, qid, text):
    path = run / "searches" / profile / f"{qid}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "results": [
                    {"rank": 1, "url": "https://a.org", "title": "", "text": text}
                ]
            }
        )
    )


def write_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_unchanged_contexts_reuse_the_baseline_answer_under_the_new_profile(tmp_path):
    base, out = tmp_path / "base", tmp_path / "out"
    for qid, old, new in (("q1", "same", "same"), ("q2", "old", "new")):
        write_search(base, "desearch-index", qid, old)
        write_search(out, "exp", qid, new)
    keys = ["desearch-index/q1", "desearch-index/q2", "no-search/q1", "exa-fast/q1"]
    write_rows(
        base / "answers" / grades_name(),
        [{"key": k, "profile": k.split("/")[0], "grade": "CORRECT"} for k in keys],
    )
    same = quick_eval.reuse_answers(base, out, "desearch-index", "exp", {"q1", "q2"})
    assert same == {"q1"}
    got = [
        json.loads(l)["key"]
        for l in (out / "answers" / grades_name()).read_text().splitlines()
    ]
    assert got == ["exp/q1", "no-search/q1"]
    quick_eval.reuse_answers(base, out, "desearch-index", "exp", {"q1", "q2"})
    assert len((out / "answers" / grades_name()).read_text().splitlines()) == 2


def judged(qid, page, text, gold=False):
    return {
        "question_id": qid,
        "search_status": "ok",
        "results": [
            {
                "mirror": False,
                "gold": gold,
                "page": {"hit": int(page)},
                "snippet": {"hit": int(text)},
            }
        ],
    }


def test_summary_compares_against_the_baseline(tmp_path):
    base, out = tmp_path / "base", tmp_path / "out"
    write_rows(base / "evaluation" / "x", [])
    (base / "evaluation" / "results.json").write_text(
        json.dumps(
            {
                "profiles": {
                    "desearch-index": [judged("q1", 1, 0), judged("q2", 1, 1, True)]
                }
            }
        )
    )
    (out / "evaluation").mkdir(parents=True)
    (out / "evaluation" / "results.json").write_text(
        json.dumps(
            {"profiles": {"exp": [judged("q1", 1, 1), judged("q2", 1, 1, True)]}}
        )
    )
    write_rows(
        base / "answers" / grades_name(),
        [
            {"key": "desearch-index/q1", "grade": "INCORRECT"},
            {"key": "desearch-index/q2", "grade": "CORRECT"},
        ],
    )
    write_rows(
        out / "answers" / grades_name(),
        [{"key": "exp/q1", "grade": "CORRECT"}, {"key": "exp/q2", "grade": "CORRECT"}],
    )
    write_search(out, "exp", "q1", "x")
    text = quick_eval.summary(base, out, "desearch-index", "exp", ["q1", "q2"])
    assert "| baseline desearch-index | 2 | 50.0% | 100.0% | 50.0% | 50.0% |" in text
    assert "| this run exp | 2 | 100.0% | 100.0% | 100.0% | 50.0% |" in text
    assert "Answers vs baseline: +1 / -0; text@10 +1 / -0." in text
