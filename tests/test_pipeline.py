import argparse
import asyncio
import json

from evaluators import grade, source_report
from scripts import upload_to_hf


def make_run(root):
    root.mkdir()
    question = {
        "id": "q1",
        "question": "Who led Nelo's seed round in April 2021?",
        "answer": "Homebrew",
    }
    (root / "questions.jsonl").write_text(json.dumps(question) + "\n")
    (root / "config.json").write_text(
        json.dumps(
            {
                "profiles": [
                    {"id": "desearch", "transport": "local"},
                    {"id": "exa", "transport": "openrouter"},
                ]
            }
        )
    )
    results = {
        "desearch": [
            {
                "url": "https://a.example/nelo",
                "title": "Nelo raises $3M",
                "text": "Homebrew led the round.",
            }
        ],
        "exa": [
            {
                "url": "https://b.example/other",
                "title": "Fintech news",
                "text": "Nothing relevant here.",
            },
            {"url": "https://a.example/nelo", "title": "Nelo", "text": "A seed round."},
            {
                "url": "https://c.example/later",
                "title": "Later",
                "text": "Homebrew again.",
            },
        ],
    }
    for profile, rows in results.items():
        target = root / "searches" / profile
        target.mkdir(parents=True)
        body = {"query": question["question"], "status": "ok", "results": rows}
        (target / "q1.json").write_text(json.dumps(body))


def test_search_artifacts_to_scores_and_export(tmp_path, monkeypatch):
    run = tmp_path / "run"
    make_run(run)
    pages = {
        "https://a.example/nelo": {
            "status": "ok",
            "title": "Nelo raises $3M",
            "text": "Nelo raised $3 million. Homebrew led the seed round.",
        },
        "https://b.example/other": {"status": "blocked", "title": "", "text": ""},
        "https://c.example/later": {
            "status": "ok",
            "title": "Later",
            "text": "Homebrew again.",
        },
    }

    async def fake_fetch(urls, **kwargs):
        return {url: pages[url] for url in urls}

    async def fake_chat(session, key, model, messages, **kwargs):
        inputs = json.loads(messages[1]["content"])
        text = " ".join(section["text"] for section in inputs["sections"])
        found = "Homebrew" in text
        body_ids = [s["id"] for s in inputs["sections"] if "Homebrew" in s["text"]]
        payload = {
            "explanation": "stated" if found else "absent",
            "extracted_answer": "Homebrew" if found else None,
            "highlights": body_ids if found else [],
            "label": "answers" if found else "no_answer",
        }
        return {
            "content": json.dumps(payload),
            "attempts": [{"response": {"usage": {"cost": 0.001}}}],
        }

    monkeypatch.setattr(grade.pages, "fetch_pages", fake_fetch)
    monkeypatch.setattr(grade.judge, "chat", fake_chat)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    args = argparse.Namespace(
        run=run,
        out=run / "evaluation",
        page_cache=run / "page-cache",
        model="judge",
        concurrency=2,
        fetch_concurrency=2,
        batch_size=5,
        env_file=tmp_path / "none.env",
    )
    asyncio.run(grade.execute(args))
    report = source_report.write_report(run / "evaluation")

    desearch = report["profiles"]["desearch"]["metrics"]
    exa = report["profiles"]["exa"]["metrics"]
    assert desearch["page_hit_at_1"] == 1 and desearch["snippet_hit_at_1"] == 1
    assert exa["page_hit_at_1"] == 0 and exa["page_hit_at_5"] == 1
    assert exa["snippet_hit_at_1"] == 0 and exa["snippet_hit_at_10"] == 1
    assert report["profiles"]["exa"]["pages_fetched"] == 2

    files = upload_to_hf.build_export(run, "test-run")
    rows = [
        json.loads(line)
        for line in files["search/runs/test-run/results.jsonl"].decode().splitlines()
    ]
    exa_row = next(row for row in rows if row["profile_id"] == "exa")
    page = exa_row["results"][1]["page"]
    assert page["label"] == "answers" and "Homebrew" in page["evidence_text"]
    assert exa_row["results"][2]["page"]["status"] == "after_first_hit"
    assert page["highlights"][0]["quote"] in page["evidence_text"]
    assert exa_row["metrics"]["page_mrr"] == 0.5
