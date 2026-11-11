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
        "max_completion_tokens": 5,
        "temperature": 0,
        "reasoning_effort": "none",
    }
    off = judge.openai_body({**body, "reasoning": {"enabled": False}})
    assert off["reasoning_effort"] == "none"
    low = judge.openai_body({**body, "reasoning": {"effort": "low"}})
    assert low["reasoning_effort"] == "low" and "temperature" not in low


def test_route_is_openrouter_unless_asked(monkeypatch):
    monkeypatch.delenv("GRADE_VIA", raising=False)
    assert (
        judge.route("openai/gpt-4.1", "k", {"model": "openai/gpt-4.1"})[0] == judge.API
    )
    monkeypatch.setenv("GRADE_VIA", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    url, key, sent = judge.route("openai/gpt-4.1", "k", {"model": "openai/gpt-4.1"})
    assert (url, key, sent["model"]) == (judge.OPENAI_API, "o", "gpt-4.1")
    assert judge.route("google/gemini-2.5-flash", "k", {})[0] == judge.API


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


def test_grader_model_overrides_every_benchmark_and_its_grades_file(monkeypatch):
    monkeypatch.setenv("GRADER_MODEL", "openai/gpt-5.4-mini")
    for benchmark in run_answers.GRADERS:
        body = run_answers.grader_request(benchmark, "q", "t", "r")
        assert body["model"] == "openai/gpt-5.4-mini"
        assert body["reasoning"] == {"effort": "none"}
    assert run_answers.grades_name() == "grades-gpt-5.4-mini.jsonl"
    monkeypatch.delenv("GRADER_MODEL")
    assert run_answers.grades_name() == "grades.jsonl"
