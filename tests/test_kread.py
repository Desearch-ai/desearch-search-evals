import json

from scripts import kread


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


def test_bank_keys_reads_by_context_and_grader(tmp_path):
    bank = kread.Bank(tmp_path / "bank.jsonl", "gpt-5.4-mini")
    bank.add({"qid": "q1", "ctx": "h", "grade": "CORRECT"})
    bank.add({"qid": "q1", "ctx": "h", "grade": "INCORRECT"})
    other = kread.Bank(tmp_path / "bank.jsonl", "gpt-4.1")
    assert other.scores("q1", "h") == []
    again = kread.Bank(tmp_path / "bank.jsonl", "gpt-5.4-mini")
    assert again.scores("q1", "h") == [1.0, 0.0]


def test_paired_copies_unchanged_and_averages_changed(tmp_path):
    base, cand = tmp_path / "base", tmp_path / "cand"
    for qid, old, new in (("q1", "same", "same"), ("q2", "old", "new")):
        write_search(base, "a", qid, old)
        write_search(cand, "b", qid, new)
    bank = kread.Bank(tmp_path / "bank.jsonl", "g")
    h_old = kread.ctx_hash(kread.search_ctx(base, "a", "q2"))
    h_new = kread.ctx_hash(kread.search_ctx(cand, "b", "q2"))
    for grade in ("CORRECT", "INCORRECT", "INCORRECT", "INCORRECT"):
        bank.add({"qid": "q2", "ctx": h_old, "grade": grade})
    for grade in ("CORRECT", "CORRECT", "CORRECT", "INCORRECT"):
        bank.add({"qid": "q2", "ctx": h_new, "grade": grade})
    questions = [
        {"id": "q1", "benchmark": "sealqa"},
        {"id": "q2", "benchmark": "sealqa"},
    ]
    rows = {
        r["qid"]: r
        for r in kread.paired(bank, questions, (base, "a"), (cand, "b"), k=4)
    }
    assert rows["q1"] == {
        "qid": "q1",
        "benchmark": "sealqa",
        "changed": False,
        "d": 0.0,
    }
    assert rows["q2"]["d"] == 0.5 and rows["q2"]["single"] == 0.0


def test_bootstrap_is_seeded_and_brackets_the_mean():
    diffs = [1.0] * 30 + [0.0] * 70
    est, lo, hi = kread.bootstrap(diffs, rounds=2000)
    assert est == 0.3 and lo < 0.3 < hi
    assert kread.bootstrap(diffs, rounds=2000) == (est, lo, hi)
    assert (
        kread.verdict(0.01, 0.2) == "win" and kread.verdict(-0.1, 0.2) == "unresolved"
    )


def test_aa_splits_one_context_into_two_disjoint_arms(tmp_path):
    run = tmp_path / "run"
    bank = kread.Bank(tmp_path / "bank.jsonl", "g")
    questions = []
    for i in range(40):
        qid = f"q{i}"
        write_search(run, "p", qid, f"ctx {i}")
        h = kread.ctx_hash(kread.search_ctx(run, "p", qid))
        for j in range(4):
            bank.add(
                {
                    "qid": qid,
                    "ctx": h,
                    "grade": "CORRECT" if (i + j) % 2 else "INCORRECT",
                }
            )
        questions.append({"id": qid, "benchmark": "frames"})
    result = kread.aa(bank, questions, run, "p", k=2, resamples=5)
    assert result["questions"] == 40 and result["resamples"] == 5
    assert all(r["verdict"] in ("win", "loss", "unresolved") for r in result["runs"])
