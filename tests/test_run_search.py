import argparse
import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from providers import run_search
from providers.desearch import load_local
from providers.run_search import prepare


@pytest.fixture(autouse=True)
def credentials(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-key")


def test_resume_rejects_changed_questions(tmp_path):
    question = {"id": "q1", "question": "First?"}
    config = {"profiles": [{"id": "local", "transport": "local", "mode": "fast"}]}
    prepare(tmp_path, [question], config)
    prepare(tmp_path, [question], config)
    with pytest.raises(ValueError, match="questions or config changed"):
        prepare(tmp_path, [{**question, "question": "Second?"}], config)


def test_import_rejects_unmatched_local_query(tmp_path):
    source = tmp_path / "local.jsonl"
    source.write_text(
        json.dumps({"question_id": "q1", "profile": "local", "query": "Wrong?"})
    )
    with pytest.raises(ValueError, match="query differs"):
        load_local(
            source,
            [{"id": "q1", "question": "Right?"}],
            [{"id": "local", "transport": "local", "mode": "fast"}],
        )
    assert not (tmp_path / "searches").exists()


def test_rejects_unsafe_artifact_paths(tmp_path):
    with pytest.raises(ValueError, match="safe artifact"):
        prepare(tmp_path, [{"id": "../q", "question": "Q?"}], {"profiles": []})


def arguments(tmp_path):
    question = {
        "id": "q1",
        "question": "Who won?",
        "answer": "Private reference answer",
        "reference_sources": [{"url": "https://example.org/gold"}],
    }
    profile = {
        "id": "exa-fast",
        "transport": "exa",
        "mode": "fast",
    }
    questions = tmp_path / "questions.jsonl"
    questions.write_text(json.dumps(question) + "\n")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"profiles": [profile]}))
    env = tmp_path / "empty.env"
    env.write_text("")
    return argparse.Namespace(
        questions=questions,
        config=config,
        run=tmp_path / "run",
        env_file=env,
        profiles=None,
        import_local=None,
        limit=None,
        concurrency=2,
        batch_size=20,
    )


def test_search_runner_sends_question_only_and_resumes_without_calls(
    tmp_path, monkeypatch
):
    args = arguments(tmp_path)
    result = {
        "query": "Who won?",
        "results": [{"rank": 1, "url": "https://example.org/result", "text": "A"}],
        "cost_usd": 0.01,
    }
    request = AsyncMock(return_value=result)
    monkeypatch.setattr(run_search.search, "search", request)
    asyncio.run(run_search.execute(args))

    request.assert_awaited_once()
    _, question, profile = request.call_args.args
    assert question == "Who won?"
    assert "answer" not in profile and "reference_sources" not in profile
    saved = args.run / "searches/exa-fast/q1.json"
    before = saved.read_bytes()
    assert json.loads(before)["status"] == "ok"
    monkeypatch.delenv("OPENROUTER_API_KEY")

    execution = asyncio.run(run_search.execute(args))
    request.assert_awaited_once()
    assert saved.read_bytes() == before
    assert execution["cost_usd"] == 0.01
    assert execution["processed"] == 0


def test_search_failure_retains_question_and_profile(tmp_path, monkeypatch):
    args = arguments(tmp_path)
    request = AsyncMock(side_effect=TimeoutError)
    monkeypatch.setattr(run_search.search, "search", request)
    summary = asyncio.run(run_search.execute(args))

    row = json.loads((args.run / "searches/exa-fast/q1.json").read_text())
    assert row["status"] == "error"
    assert row["query"] == "Who won?"
    assert row["question_id"] == "q1"
    assert row["profile_id"] == "exa-fast"
    assert row["results"] == []
    assert row["cost_usd"] is None
    assert summary["cost_usd"] is None
    assert summary["recorded_cost_usd"] == 0
    assert summary["unknown_cost_searches"] == 1


def test_interrupted_batch_resumes_only_missing_results(tmp_path, monkeypatch):
    args = arguments(tmp_path)
    args.batch_size = 1
    args.questions.write_text(
        args.questions.read_text()
        + json.dumps({"id": "q2", "question": "Who lost?"})
        + "\n"
    )
    request = AsyncMock(
        side_effect=[{"results": [], "cost_usd": 0.01}, asyncio.CancelledError]
    )
    monkeypatch.setattr(run_search.search, "search", request)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run_search.execute(args))
    saved = {
        path: path.read_bytes() for path in (args.run / "searches").glob("*/*.json")
    }
    assert len(saved) == 1
    request.reset_mock()
    request.side_effect = None
    request.return_value = {"results": [], "cost_usd": 0.02}
    resumed = asyncio.run(run_search.execute(args))
    request.assert_awaited_once()
    assert resumed["cost_usd"] == pytest.approx(0.03)
    assert len(list((args.run / "searches").glob("*/*.json"))) == 2
    assert all(path.read_bytes() == value for path, value in saved.items())


def test_unknown_selected_profile_fails_before_creating_run(tmp_path):
    args = arguments(tmp_path)
    args.profiles = "typo"
    with pytest.raises(ValueError, match="absent"):
        asyncio.run(run_search.execute(args))
    assert not args.run.exists()


def test_selected_profiles_and_question_limit_are_frozen_consistently(
    tmp_path, monkeypatch
):
    args = arguments(tmp_path)
    config = json.loads(args.config.read_text())
    config["profiles"].append({"id": "local", "transport": "local", "mode": "fast"})
    args.config.write_text(json.dumps(config))
    args.questions.write_text(
        args.questions.read_text()
        + json.dumps({"id": "q2", "question": "Other?"})
        + "\n"
    )
    args.profiles = "exa-fast"
    args.limit = 1
    monkeypatch.setattr(
        run_search.search,
        "search",
        AsyncMock(return_value={"results": [], "cost_usd": 0}),
    )
    asyncio.run(run_search.execute(args))
    assert len(run_search.read_jsonl(args.run / "questions.jsonl")) == 1
    assert [
        p["id"] for p in json.loads((args.run / "config.json").read_text())["profiles"]
    ] == ["exa-fast"]


@pytest.mark.parametrize(
    "batch_size, concurrency", [(0, 1), (-1, 1), (1.5, 1), (1, 0), (1, 33)]
)
def test_invalid_limits_fail_before_requests(
    tmp_path, monkeypatch, batch_size, concurrency
):
    args = arguments(tmp_path)
    args.batch_size, args.concurrency = batch_size, concurrency
    request = AsyncMock()
    monkeypatch.setattr(run_search.search, "search", request)
    with pytest.raises(ValueError):
        asyncio.run(run_search.execute(args))
    request.assert_not_awaited()
    assert not args.run.exists()


@pytest.mark.parametrize(
    "results", ["malformed", [{"rank": 2, "text": "Answer"}], [{"text": 23}]]
)
def test_malformed_adapter_results_become_resumable_failures(
    tmp_path, monkeypatch, results
):
    args = arguments(tmp_path)
    request = AsyncMock(
        return_value={
            "results": results,
            "cost_usd": 0.02,
            "raw": {"provider": "response"},
        }
    )
    monkeypatch.setattr(run_search.search, "search", request)
    asyncio.run(run_search.execute(args))
    row = json.loads((args.run / "searches/exa-fast/q1.json").read_text())
    assert row["status"] == "error"
    assert row["results"] == []
    assert row["invalid_result"]["results"] == results
    assert row["raw"] == {"provider": "response"}
    assert row["cost_usd"] == 0.02
    asyncio.run(run_search.execute(args))
    request.assert_awaited_once()
