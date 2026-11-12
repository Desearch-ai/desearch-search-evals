import pytest

from evaluators import judge
from scripts import run_answers


def test_openai_body_drops_openrouter_fields_and_keeps_effort():
    body = {
        "model": "openai/gpt-5.4-mini",
        "messages": [],
        "max_tokens": 5,
        "temperature": 0,
        "reasoning": {"effort": "none"},
    }
    sent = judge.openai_body(body)
    assert len(sent.pop("prompt_cache_key")) == 16
    assert sent == {
        "model": "gpt-5.4-mini",
        "messages": [],
        "max_completion_tokens": 16,
        "temperature": 0,
        "reasoning_effort": "none",
    }
    off = judge.openai_body({**body, "reasoning": {"enabled": False}})
    assert off["reasoning_effort"] == "none"
    low = judge.openai_body({**body, "reasoning": {"effort": "low"}})
    assert low["reasoning_effort"] == "low" and "temperature" not in low


def test_route_is_openai_unless_openrouter_is_asked(monkeypatch):
    monkeypatch.delenv("GRADE_VIA", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    url, key, sent = judge.route("openai/gpt-4.1", "k", {"model": "openai/gpt-4.1"})
    assert (url, key, sent["model"]) == (judge.OPENAI_API, "o", "gpt-4.1")
    assert judge.route("google/gemini-2.5-flash", "k", {})[0] == judge.API
    monkeypatch.setenv("GRADE_VIA", "openrouter")
    assert (
        judge.route("openai/gpt-4.1", "k", {"model": "openai/gpt-4.1"})[0] == judge.API
    )
    monkeypatch.setenv("GRADE_VIA", "openrouter-typo")
    with pytest.raises(ValueError):
        judge.grade_via()


def test_key_for_names_the_key_of_the_active_route(monkeypatch):
    monkeypatch.delenv("GRADE_VIA", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    monkeypatch.setenv("OPENROUTER_API_KEY", "r")
    assert judge.key_for("openai/gpt-5.4-mini") == ("OPENAI_API_KEY", "o")
    assert judge.key_for("google/gemini-2.5-flash") == ("OPENROUTER_API_KEY", "r")
    monkeypatch.setenv("GRADE_VIA", "openrouter")
    assert judge.key_for("openai/gpt-5.4-mini") == ("OPENROUTER_API_KEY", "r")


def test_priced_counts_cached_tokens_at_their_rate():
    usage = {
        "prompt_tokens": 1000,
        "completion_tokens": 10,
        "prompt_tokens_details": {"cached_tokens": 800},
    }
    assert (
        abs(
            judge.priced("openai/gpt-5.4-mini", usage)
            - (200 * 0.75e-6 + 800 * 0.075e-6 + 10 * 4.5e-6)
        )
        < 1e-12
    )
    assert judge.priced("openai/unknown", usage) is None


def test_default_grader_is_gpt_5_4_mini_on_openai_for_every_benchmark(monkeypatch):
    for name in ("GRADER_MODEL", "GRADE_VIA", "GRADER_EFFORT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    for benchmark in run_answers.GRADERS:
        body = run_answers.grader_request(benchmark, "q", "t", "r")
        assert body["model"] == judge.DEFAULT_GRADER == "openai/gpt-5.4-mini"
        assert body["reasoning"] == {"effort": "none"}
        assert judge.route(body["model"], "k", body)[0] == judge.OPENAI_API
    assert run_answers.grades_name() == "grades-gpt-5.4-mini.jsonl"
    from evaluators import grade

    assert grade.JUDGE == judge.DEFAULT_GRADER


def test_grader_model_overrides_and_benchmark_restores_their_own(monkeypatch):
    monkeypatch.setenv("GRADER_MODEL", "openai/gpt-4.1")
    assert run_answers.grader_request("sealqa", "q", "t", "r")["model"] == "openai/gpt-4.1"
    assert run_answers.grades_name() == "grades-gpt-4.1.jsonl"
    monkeypatch.setenv("GRADER_MODEL", "benchmark")
    for benchmark, grader in run_answers.GRADERS.items():
        body = run_answers.grader_request(benchmark, "q", "t", "r")
        assert body["model"] == grader["model"]
        assert "effort" not in (body.get("reasoning") or {})
    assert run_answers.grades_name() == "grades.jsonl"
