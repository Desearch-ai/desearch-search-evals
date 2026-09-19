import asyncio
import json

import pytest

from agents import grade, loop
from agents.tools import Tools, contaminated


class FakeSearch:
    def __init__(self, results):
        self.results = results
        self.calls = []

    async def __call__(self, session, query, profile):
        self.calls.append(query)
        return {"results": self.results, "cost_usd": 0.003, "elapsed_seconds": 0.8}


def _tools(tmp_path, monkeypatch, results):
    fake = FakeSearch(results)
    monkeypatch.setattr("agents.tools.search_provider.search", fake)
    tools = Tools(
        session=None,
        profile={"id": "p", "transport": "exa", "mode": "fast"},
        cache_dir=tmp_path / "searches",
        page_cache=tmp_path / "pages",
    )
    return tools, fake


def test_search_results_are_cached_on_disk(tmp_path, monkeypatch):
    tools, fake = _tools(
        tmp_path,
        monkeypatch,
        [{"url": "https://a.example", "title": "A", "text": "x" * 4000}],
    )

    first = asyncio.run(tools.web_search("who won"))
    second = asyncio.run(tools.web_search("who won"))

    assert first == second
    assert fake.calls == ["who won"], "the second call must come from the cache"
    assert json.loads(first)[0]["excerpt"] == "x" * 1200
    assert tools.searches == 2 and tools.cost_usd == pytest.approx(0.006)
    assert tools.search_seconds == pytest.approx(1.6)


def test_benchmark_copies_never_reach_the_model(tmp_path, monkeypatch):
    tools, _ = _tools(
        tmp_path,
        monkeypatch,
        [
            {"url": "https://huggingface.co/datasets/browsecomp", "title": "leak"},
            {"url": "https://good.example/a", "title": "A", "text": "real"},
        ],
    )

    found = json.loads(asyncio.run(tools.web_search("q")))

    assert [row["url"] for row in found] == ["https://good.example/a"]
    assert contaminated("https://github.com/openai/simple-evals")


def test_empty_results_are_reported_plainly(tmp_path, monkeypatch):
    tools, _ = _tools(tmp_path, monkeypatch, [])

    assert asyncio.run(tools.web_search("q")) == "No results."
    assert asyncio.run(tools.web_search("  ")) == "web_search needs a query."


class FakeModel:
    """Plays a scripted sequence of tool calls, one per turn."""

    def __init__(self, script):
        self.script = list(script)
        self.seen = []

    async def __call__(self, session, key, model, messages, effort):
        self.seen.append(messages[-1])
        step = self.script.pop(0)
        calls = [
            {
                "id": f"call-{index}",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }
            for index, (name, arguments) in enumerate(step)
        ]
        return {
            "choices": [{"message": {"role": "assistant", "tool_calls": calls}}],
            "usage": {"cost": 0.01},
        }


class StubTools:
    searches = 2
    fetches = 1
    cost_usd = 0.006
    search_seconds = 1.5

    async def web_search(self, query):
        return f"results for {query}"

    async def web_fetch(self, url):
        return f"text of {url}"


def test_agent_searches_then_finishes(monkeypatch):
    model = FakeModel(
        [
            [("web_search", {"query": "first"})],
            [("web_fetch", {"url": "https://a.example"})],
            [("finish", {"answer": "Serban Ghenea"})],
        ]
    )
    monkeypatch.setattr(loop, "call_model", model)

    outcome = asyncio.run(
        loop.run(None, "key", "who won?", StubTools(), model="m", turns=25)
    )

    assert outcome["answer"] == "Serban Ghenea"
    assert outcome["turns"] == 3
    assert outcome["model_cost_usd"] == pytest.approx(0.03)
    assert outcome["search_cost_usd"] == pytest.approx(0.006)
    assert outcome["search_seconds"] == pytest.approx(1.5)
    assert outcome["seconds"] >= 0 and outcome["model_seconds"] >= 0
    assert model.seen[1]["content"] == "results for first"
    assert model.seen[2]["content"] == "text of https://a.example"


def test_running_out_of_turns_submits_no_answer(monkeypatch):
    model = FakeModel([[("web_search", {"query": "again"})] for _ in range(3)])
    monkeypatch.setattr(loop, "call_model", model)

    outcome = asyncio.run(loop.run(None, "key", "q", StubTools(), model="m", turns=3))

    assert outcome["answer"] is None and outcome["turns"] == 3


def _judge(monkeypatch, content):
    async def chat(session, key, model, messages, max_tokens=1600, json_mode=True):
        return {"content": content, "recorded_cost_usd": 0.0001}

    monkeypatch.setattr(grade.judge, "chat", chat)


def test_exact_answer_grading(monkeypatch):
    _judge(monkeypatch, '{"correct": true, "reason": "same person"}')
    scored = asyncio.run(
        grade.exact_answer(None, "k", "m", "q", "Serban Ghenea", "Serban Ghenea")
    )
    assert scored["score"] == 1.0 and scored["graded"]

    assert (
        asyncio.run(grade.exact_answer(None, "k", "m", "q", None, "x"))["score"] == 0.0
    )


def test_answer_set_grading_is_f1_over_items(monkeypatch):
    _judge(
        monkeypatch,
        '{"matched": ["Dareeyak Teleport"], "extra": ["Lumbridge Teleport"]}',
    )

    scored = asyncio.run(
        grade.answer_set(
            None,
            "k",
            "m",
            "q",
            "Dareeyak Teleport; Lumbridge Teleport",
            "Dareeyak Teleport; Ghorrock Teleport",
        )
    )

    assert scored["precision"] == pytest.approx(0.5)
    assert scored["recall"] == pytest.approx(0.5)
    assert scored["score"] == pytest.approx(0.5)


def test_grader_outage_is_not_scored_as_wrong(monkeypatch):
    async def failing(*args, **kwargs):
        return {"error": "request_failed"}

    monkeypatch.setattr(grade.judge, "chat", failing)

    scored = asyncio.run(
        grade.exact_answer(None, "k", "m", "q", "an answer", "an answer")
    )

    assert scored["graded"] is False


def test_agent_export_matches_the_shape_the_ui_reads(tmp_path):
    from scripts.upload_to_hf import build_agent_export

    run = tmp_path / "browsecomp"
    run.mkdir()
    (run / "questions.jsonl").write_text(
        json.dumps(
            {"id": "q1", "question": "Q?", "answer": "A", "benchmark": "browsecomp"}
        )
        + "\n"
    )
    (run / "config.json").write_text(
        json.dumps(
            {
                "profiles": [
                    {
                        "id": "exa-fast",
                        "transport": "exa",
                        "mode": "fast",
                    }
                ]
            }
        )
    )
    (run / "answers.jsonl").write_text(
        json.dumps(
            {
                "question_id": "q1",
                "profile_id": "exa-fast",
                "status": "ok",
                "answer": "A",
                "score": 1.0,
                "searches": 9,
                "turns": 4,
                "model_cost_usd": 0.02,
            }
        )
        + "\n"
    )
    (run / "report.json").write_text(
        json.dumps(
            {
                "questions": 1,
                "model": "openai/gpt-5.6-luna",
                "grader_model": "openai/gpt-5.4-mini",
                "turns": 25,
                "profiles": {
                    "exa-fast": {
                        "score": 1.0,
                        "searches_per_task": 9,
                        "turns_per_task": 4,
                        "cost_per_task_usd": 0.029,
                    }
                },
            }
        )
    )

    files = build_agent_export(run, run_id="browsecomp", label="BrowseComp")

    latest = json.loads(files["search/latest.json"])
    scoreboard = json.loads(files["search/runs/browsecomp/scoreboard.json"])
    rows = [
        json.loads(line)
        for line in files["search/runs/browsecomp/results.jsonl"].decode().splitlines()
    ]

    assert latest == {
        "run_id": "browsecomp",
        "path": "search/runs/browsecomp",
        "label": "BrowseComp",
        "note": None,
        "kind": "agent",
        "questions": 1,
        "rows": 1,
        "profiles": ["exa-fast"],
    }
    assert scoreboard["kind"] == "agent"
    assert scoreboard["profiles"]["exa-fast"]["metrics"]["score"] == 1.0
    assert rows[0]["answer"] == "A" and rows[0]["metrics"]["score"] == 1.0
    assert "69" not in files["search/runs/browsecomp/report.md"].decode()


def test_failed_searches_are_not_cached_or_shown_as_empty(tmp_path, monkeypatch):
    calls = []

    async def failing(session, query, profile):
        calls.append(query)
        raise RuntimeError("Tavily returned 429")

    monkeypatch.setattr("agents.tools.search_provider.search", failing)
    tools = Tools(
        session=None,
        profile={"id": "p", "transport": "tavily", "mode": "basic"},
        cache_dir=tmp_path / "searches",
        page_cache=tmp_path / "pages",
    )

    first = asyncio.run(tools.web_search("q"))
    asyncio.run(tools.web_search("q"))

    assert first == "Search failed. Try again or rephrase."
    assert calls == ["q", "q"], "a failure must be retried, not served from cache"
    assert tools.search_errors == 2 and tools.searches == 0
    assert not list((tmp_path / "searches").glob("*.json"))
