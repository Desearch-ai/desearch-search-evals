import json

from providers import parallel, perplexity
from scripts import latency_report, write_queries


def test_server_seconds_reads_each_providers_own_timing():
    assert abs(latency_report.server_seconds({"raw": {"searchTime": 1472.8}}) - 1.4728) < 1e-9
    assert latency_report.server_seconds({"raw": {"response_time": 2.22}}) == 2.22
    assert latency_report.server_seconds({"server_ms": {"total": 930.0}}) == 0.93
    assert latency_report.server_seconds({"server_headers": {"server-timing": "db;dur=12.5, app;dur=300"}}) == 0.3125
    assert latency_report.server_seconds({"server_headers": {"x-response-time": "250ms"}}) == 0.25
    assert latency_report.server_seconds({"raw": {"id": "x"}}) is None


def test_parallel_uses_written_objective_and_queries(tmp_path):
    path = tmp_path / "q.json"
    path.write_text(json.dumps({"Who won?": {"objective": "Find the winner.", "search_queries": ["race winner 2024"]}}))
    profile = {"transport": "parallel", "mode": "fast", "queries_file": str(path)}
    parallel.validate_profile(profile)
    body = parallel.build_request("Who won?", profile)
    assert body["objective"] == "Find the winner." and body["search_queries"] == ["race winner 2024"]
    assert parallel.build_request("Other?", profile)["search_queries"] == ["Other?"]


def test_perplexity_long_budget_overrides_the_per_result_cap():
    profile = {"transport": "perplexity", "mode": "web", "max_characters": 16000, "max_tokens": 10000, "max_tokens_per_page": 4000}
    body = perplexity.build_request("q", profile)
    assert body["max_tokens"] == 10000 and body["max_tokens_per_page"] == 4000
    assert perplexity.build_request("q", {"transport": "perplexity", "mode": "web"})["max_tokens_per_page"] == 500


def test_written_queries_parse_keeps_at_most_three():
    got = write_queries.parse('Sure: {"objective": "Find X.", "search_queries": ["a b c", "d e f", "g h i", "j k l"]}')
    assert got == {"objective": "Find X.", "search_queries": ["a b c", "d e f", "g h i"]}
    assert write_queries.parse("no json") is None
