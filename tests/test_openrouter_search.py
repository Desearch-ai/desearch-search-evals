import copy
import unittest

from providers.openrouter import build_request, parse_response, sanitize, search

QUESTION = "Which company acquired DoubleClick in 2008?"
PROFILE = {"engine": "perplexity", "max_results": 10}


def response_fixture():
    return {
        "status": "completed",
        "error": None,
        "usage": {
            "cost": 0.008,
            "server_tool_use_details": {
                "web_search_requests": 1,
                "tool_calls_executed": 1,
            },
        },
        "output": [
            {
                "type": "openrouter:web_search",
                "status": "completed",
                "action": {
                    "type": "search",
                    "query": QUESTION,
                    "sources": [
                        {"type": "url", "url": "https://example.org/first"},
                        {"type": "url", "url": "https://example.org/second"},
                    ],
                },
            },
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": "DONE",
                        "annotations": [
                            {
                                "type": "url_citation",
                                "url": "https://example.org/second",
                                "title": "Second",
                                "content": "Second provider excerpt.",
                            },
                            {
                                "type": "url_citation",
                                "url": "https://example.org/first",
                                "title": "First",
                                "content": "First provider excerpt.",
                            },
                        ],
                    }
                ],
            },
        ],
    }


class OpenRouterParsingTests(unittest.TestCase):
    def test_rank_comes_from_search_action_not_annotation_order(self):
        parsed = parse_response(response_fixture(), QUESTION, PROFILE)
        self.assertNotIn("error", parsed)
        self.assertEqual(parsed["results"][0]["url"], "https://example.org/first")
        self.assertEqual(parsed["results"][0]["text"], "First provider excerpt.")

    def test_duplicate_results_consume_rank_positions(self):
        data = response_fixture()
        sources = data["output"][0]["action"]["sources"]
        sources.insert(1, copy.deepcopy(sources[0]))
        parsed = parse_response(data, QUESTION, PROFILE)
        self.assertEqual([r["rank"] for r in parsed["results"]], [1, 2, 3])
        self.assertEqual(parsed["results"][2]["url"], "https://example.org/second")

    def test_answer_citations_cannot_substitute_for_missing_search_ranking(self):
        data = response_fixture()
        del data["output"][0]["action"]["sources"]
        parsed = parse_response(data, QUESTION, PROFILE)
        self.assertIn("ranking is unavailable", parsed["error"])
        self.assertEqual(parsed["results"], [])

    def test_rewritten_query_is_not_scored_as_original_query(self):
        data = response_fixture()
        data["output"][0]["action"]["query"] = "DoubleClick acquirer 2008"
        parsed = parse_response(data, QUESTION, PROFILE)
        self.assertIn("changed", parsed["error"])
        self.assertEqual(parsed["results"], [])

    def test_missing_excerpt_cannot_be_filled_from_model_answer(self):
        data = response_fixture()
        content = data["output"][1]["content"][0]
        content["text"] = "The answer is Google."
        del content["annotations"][0]["content"]
        parsed = parse_response(data, QUESTION, PROFILE)
        self.assertIn("no attached retrieval excerpt", parsed["error"])
        self.assertEqual(parsed["results"], [])

    def test_empty_search_is_valid_if_execution_is_verified(self):
        data = response_fixture()
        data["output"][0]["action"]["sources"] = []
        data["output"][1]["content"][0]["annotations"] = []
        parsed = parse_response(data, QUESTION, PROFILE)
        self.assertNotIn("error", parsed)
        self.assertEqual(parsed["results"], [])

    def test_multiple_or_unverified_searches_fail(self):
        for counts in ({"web_search_requests": 2}, {}):
            with self.subTest(counts=counts):
                data = response_fixture()
                data["usage"]["server_tool_use_details"] = counts
                parsed = parse_response(data, QUESTION, PROFILE)
                self.assertIn("exactly one", parsed["error"])

    def test_null_usage_preserves_explicit_failures(self):
        for usage in (
            None,
            {"server_tool_use": None},
            {"server_tool_use_details": None},
        ):
            with self.subTest(usage=usage):
                data = response_fixture()
                data["usage"] = usage
                result = parse_response(data, QUESTION, PROFILE)
                self.assertIn("exactly one", result["error"])
                self.assertIsNone(result["cost_usd"])
                self.assertEqual(result["results"], [])
        result = parse_response(
            {"status": "failed", "error": {}, "usage": None}, QUESTION, PROFILE
        )
        self.assertIn("failed", result["error"])
        self.assertIsNone(result["cost_usd"])

    def test_conflicting_annotations_fail_instead_of_arbitrary_selection(self):
        data = response_fixture()
        annotations = data["output"][1]["content"][0]["annotations"]
        duplicate = copy.deepcopy(annotations[0])
        duplicate["content"] = "Different excerpt for an identical URL."
        annotations.append(duplicate)
        self.assertIn("Conflicting", parse_response(data, QUESTION, PROFILE)["error"])

    def test_all_authorized_profiles_use_one_search(self):
        for engine, mode in (("perplexity", None),):
            with self.subTest(engine=engine, mode=mode):
                request = build_request(QUESTION, {"engine": engine, "mode": mode})
                parameters = request["tools"][0]["parameters"]
                self.assertEqual(parameters["max_uses"], 1)
                self.assertEqual(request["max_tool_calls"], 1)
                self.assertEqual(request["input"], QUESTION)
                self.assertEqual(parameters.get("mode"), mode)

    def test_deep_or_unverified_modes_fail_before_network_call(self):
        for profile in (
            {"engine": "exa", "mode": "deep"},
            {"engine": "perplexity", "mode": "fast"},
            {"engine": "tavily", "mode": "fast"},
            {**PROFILE, "start_date": "2026-09-01"},
        ):
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                build_request(QUESTION, profile)

    def test_sanitization_is_recursive_and_removes_key_values(self):
        raw = {"Authorization": "Bearer secret", "events": [{"text": "secret"}]}
        self.assertEqual(
            sanitize(raw, "secret"),
            {"Authorization": "[REDACTED]", "events": [{"text": "[REDACTED]"}]},
        )


class FakeResponse:
    def __init__(self, body, status=200):
        self.body = body
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def json(self, **kwargs):
        return self.body


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, endpoint, **kwargs):
        self.calls.append((endpoint, kwargs))
        return self.response


class OpenRouterTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_one_http_request_and_safe_raw_artifacts(self):
        session = FakeSession(FakeResponse(response_fixture()))
        parsed = await search(session, QUESTION, PROFILE, "private-key")
        self.assertEqual(len(session.calls), 1)
        self.assertNotIn("error", parsed)
        self.assertNotIn("private-key", str(parsed))
        self.assertIn("response", parsed["raw"])
        self.assertGreaterEqual(parsed["elapsed_seconds"], 0)

    async def test_http_failure_remains_explicit_without_fallback(self):
        body = {"error": {"message": "Denied private-key"}}
        session = FakeSession(FakeResponse(body, 429))
        parsed = await search(session, QUESTION, PROFILE, "private-key")
        self.assertIn("HTTP 429", parsed["error"])
        self.assertNotIn("private-key", str(parsed))
        self.assertEqual(parsed["results"], [])
        self.assertEqual(len(session.calls), 1)


if __name__ == "__main__":
    unittest.main()
