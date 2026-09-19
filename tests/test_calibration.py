import argparse
import json

from scripts import calibration


def basis(evidence, status="ok", hit=0, screen="exact", label="no_answer"):
    return {
        "status": status,
        "hit": hit,
        "screen": screen,
        "evidence": evidence,
        "judgment": {"label": label},
    }


def make_evaluation(root):
    evaluation = root / "evaluation"
    evaluation.mkdir(parents=True)
    (root / "questions.jsonl").write_text(
        json.dumps({"id": "q1", "question": "Who won?", "answer": "Orin"}) + "\n"
    )
    results = {
        "evidence": {f"e{i}": {"title": "T", "text": f"text {i}"} for i in range(8)},
        "profiles": {
            "a": [
                {
                    "question_id": "q1",
                    "results": [
                        {
                            "page": basis("e0", hit=1, label="answers"),
                            "snippet": basis(
                                "e1", status="unsupported_answer", label="answers"
                            ),
                        },
                        {"page": basis("e2"), "snippet": basis("e3", screen="none")},
                        {
                            "page": {"status": "not_judged", "evidence": None},
                            "snippet": None,
                        },
                    ],
                }
            ],
            "b": [
                {
                    "question_id": "q1",
                    "results": [
                        {
                            "page": basis("e0", hit=1, label="answers"),
                            "snippet": basis("e4", hit=1, label="answers"),
                        },
                    ],
                }
            ],
        },
    }
    (evaluation / "results.json").write_text(json.dumps(results))
    return evaluation


def test_cases_are_blind_deduplicated_and_stratified(tmp_path):
    evaluation = make_evaluation(tmp_path)
    out = tmp_path / "calibration" / "cases.jsonl"
    calibration.sample(argparse.Namespace(evaluation=evaluation, out=out, n=8, seed=1))
    cases = [json.loads(line) for line in out.read_text().splitlines()]
    key = json.loads(out.with_suffix(".key.json").read_text())
    assert len(cases) == 5
    for case in cases:
        assert not {"judge_hit", "judge_label", "stratum", "profiles"} & set(case)
    assert key["stratum_sizes"] == {
        "accepted": 2,
        "unsupported": 1,
        "rejected_with_match": 1,
        "rejected_without_match": 1,
    }
    shared = next(v for v in key["cases"].values() if v["evidence"] == "e0")
    assert shared["profiles"] == ["a", "b"]


def test_scores_weight_strata_by_their_size():
    key = {
        "stratum_sizes": {
            "accepted": 100,
            "unsupported": 10,
            "rejected_with_match": 50,
            "rejected_without_match": 400,
        },
        "cases": {
            "c1": {"stratum": "accepted", "judge_hit": 1},
            "c2": {"stratum": "accepted", "judge_hit": 1},
            "c3": {"stratum": "rejected_without_match", "judge_hit": 0},
            "c4": {"stratum": "rejected_without_match", "judge_hit": 0},
        },
    }
    cases = [
        {"case_id": "c1", "human_label": "yes"},
        {"case_id": "c2", "human_label": "no"},
        {"case_id": "c3", "human_label": "yes"},
        {"case_id": "c4", "human_label": "no"},
        {"case_id": "unlabelled", "human_label": None},
    ]
    report = calibration.score(cases, key)
    assert report["labelled"] == 4
    assert report["precision_of_hits"] == 0.5
    assert report["share_of_rejections_that_answer"] == 0.5
    assert report["recall_of_hits"] == 50 / (50 + 200)


def test_model_json_is_read_through_code_fences():
    from evaluators import judge

    assert judge.parse_json('```json\n{"label": "no"}\n```') == {"label": "no"}
    assert judge.parse_json("no json here") == {}
