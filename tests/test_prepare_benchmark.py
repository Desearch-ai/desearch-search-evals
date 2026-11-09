from scripts.prepare_benchmark import select_samples


def test_remainder_holds_every_question_in_neither_sample():
    records = [
        {"id": f"q{i}", "original_index": i, "historical_partition": "held_out" if i % 2 else "development"}
        for i in range(10)
    ]
    samples = select_samples(records, 3, 2, "42")
    chosen = {r["id"] for split in ("evaluation", "calibration") for r in samples[split]}
    assert len(chosen) == 5
    assert [r["original_index"] for r in samples["remainder"]] == sorted(
        r["original_index"] for r in records if r["id"] not in chosen
    )
