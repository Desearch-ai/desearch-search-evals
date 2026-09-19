import copy
import json
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from unittest.mock import AsyncMock, patch

from providers import desearch, exa, parallel, search, tavily

QUESTION = "Which company acquired DoubleClick in 2008?"


class ProfileTests(unittest.TestCase):
    def test_each_provider_uses_its_own_transport(self):
        cases = [
            ({"transport": "exa", "mode": "fast"}, "exa"),
            ({"transport": "exa", "mode": "auto"}, "exa"),
            ({"transport": "parallel", "mode": "fast"}, "parallel"),
            ({"transport": "parallel", "mode": "advanced"}, "parallel"),
            ({"transport": "openrouter", "engine": "perplexity"}, "perplexity"),
            ({"transport": "tavily", "mode": "fast"}, "tavily"),
            ({"transport": "tavily", "mode": "basic"}, "tavily"),
            ({"transport": "local", "mode": "standard"}, "desearch"),
        ]
        with patch("providers.search.os.environ.get") as load_key:
            for profile, expected in cases:
                with self.subTest(profile=profile):
                    original = copy.deepcopy(profile)
                    self.assertEqual(search.validate_profile(profile), expected)
                    self.assertEqual(profile, original)
            load_key.assert_not_called()

    def test_retired_and_invalid_profiles_are_rejected(self):
        for profile in (
            {"transport": "openrouter", "engine": "exa", "mode": "fast"},
            {"transport": "openrouter", "engine": "parallel", "mode": "fast"},
            {"transport": "exa", "mode": "deep"},
            {"transport": "parallel", "mode": "turbo"},
            {"transport": "exa", "mode": "fast", "start_date": "2026-09-01"},
            {"transport": "local", "mode": "fast", "engine": "exa"},
            {"transport": "unknown"},
        ):
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                search.validate_profile(profile)


class DispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_provider_gets_its_own_key(self):
        for profile, key_name, adapter in (
            ({"transport": "exa", "mode": "fast"}, "EXA_API_KEY", "exa"),
            ({"transport": "parallel", "mode": "fast"}, "PARALLEL_API_KEY", "parallel"),
            (
                {"transport": "openrouter", "engine": "perplexity"},
                "OPENROUTER_API_KEY",
                "perplexity",
            ),
            ({"transport": "tavily", "mode": "basic"}, "TAVILY_API_KEY", "tavily"),
        ):
            with self.subTest(adapter=adapter):
                fake = AsyncMock(return_value={"results": []})
                with (
                    patch.dict("os.environ", {key_name: "secret"}, clear=True),
                    patch.object(search.ADAPTERS[adapter], "search", fake),
                ):
                    await search.search("session", QUESTION, profile)
                fake.assert_awaited_once_with("session", QUESTION, profile, "secret")

    async def test_missing_credentials_and_local_profiles_never_dispatch(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "EXA_API_KEY"):
                await search.search(
                    None, QUESTION, {"transport": "exa", "mode": "fast"}
                )
            with self.assertRaisesRegex(ValueError, "imported"):
                await search.search(
                    None, QUESTION, {"transport": "local", "mode": "fast"}
                )
            with self.assertRaisesRegex(ValueError, "nonempty"):
                await search.search(None, "  ", {"transport": "exa", "mode": "fast"})


class LocalDesearchTests(unittest.TestCase):
    def test_local_results_preserve_sources_and_reject_conflicting_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "local.jsonl"
            source = {
                "question_id": "q1",
                "profile": "desearch-fast",
                "query": QUESTION,
                "status": "ok",
                "results": [
                    {"rank": 1, "url": "https://example.org/a", "text": "First."},
                    {"rank": 2, "url": "https://example.org/a", "text": "Again."},
                ],
            }
            path.write_text(json.dumps(source) + "\n")
            questions = [{"id": "q1", "question": QUESTION}]
            profiles = [{"id": "desearch-fast", "transport": "local", "mode": "fast"}]
            loaded = desearch.load_local(path, questions, profiles)
            row = loaded["desearch-fast/q1.json"]
            self.assertEqual(row["results"], source["results"])
            self.assertEqual(row["query"], QUESTION)
            self.assertEqual(row["cost_usd"], 0)
            self.assertEqual(desearch.load_local(path, questions, profiles), loaded)
            original = json.dumps(source)
            source["results"][0]["text"] = "Changed."
            path.write_text(original + "\n" + json.dumps(source) + "\n")
            with self.assertRaisesRegex(ValueError, "conflicting duplicate"):
                desearch.load_local(path, questions, profiles)


class FakeResponse:
    def __init__(self, data, status=200):
        self.data, self.status = data, status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def json(self, **kwargs):
        return self.data


class FakeSession:
    def __init__(self, data, status=200):
        self.response = FakeResponse(data, status)
        self.calls = []

    def post(self, endpoint, **kwargs):
        self.calls.append((endpoint, kwargs))
        return self.response


class TavilySearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_raw_request_preserves_full_query_and_disables_synthesis(self):
        question = QUESTION * 15
        raw = {
            "query": question,
            "results": [
                {"url": "https://example.org", "title": "First", "content": "abcdef"},
                {"url": "https://example.org", "title": "Again", "content": "ghijk"},
            ],
            "usage": {"credits": 2},
        }
        session = FakeSession(raw)
        result = await tavily.search(
            session, question, {"mode": "basic", "max_characters": 5}, "test-key"
        )
        self.assertEqual(len(session.calls), 1)
        endpoint, request = session.calls[0]
        self.assertEqual(endpoint, "https://api.tavily.com/search")
        self.assertEqual(
            request["json"],
            {
                "query": question,
                "search_depth": "basic",
                "max_results": 10,
                "chunks_per_source": 3,
                "include_answer": False,
                "include_raw_content": False,
                "include_published_date": True,
                "auto_parameters": False,
                "topic": "general",
                "include_usage": True,
            },
        )
        self.assertEqual(request["headers"], {"Authorization": "Bearer test-key"})
        self.assertEqual([row["rank"] for row in result["results"]], [1, 2])
        self.assertEqual([row["text"] for row in result["results"]], ["abcde", "ghijk"])
        self.assertIsNone(result["cost_usd"])
        self.assertEqual(result["usage_credits"], 2)
        self.assertEqual(result["raw"], raw)
        self.assertEqual(result["request"], request["json"])
        self.assertEqual(result["search_calls"], 1)

    async def test_missing_usage_cost_behavior_is_preserved(self):
        for mode in ("fast", "advanced"):
            with self.subTest(mode=mode):
                session = FakeSession({}, 200)
                result = await tavily.search(
                    session, QUESTION, {"mode": mode}, "test-key"
                )
                self.assertIsNone(result["cost_usd"])
                self.assertIsNone(result["error"])
                self.assertEqual(len(session.calls), 1)

    async def test_rate_limits_are_retried_then_raised(self):
        """A 429 must never come back as an empty result set."""
        session = FakeSession({"detail": {"error": "blocked"}}, 429)
        with unittest.mock.patch("providers.tavily.asyncio.sleep") as sleep:
            sleep.return_value = None
            with self.assertRaises(RuntimeError):
                await tavily.search(session, QUESTION, {"mode": "basic"}, "test-key")
        self.assertEqual(len(session.calls), tavily.ATTEMPTS)


class ExaSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_request_keeps_the_question_and_returns_highlights(self):
        raw = {
            "results": [
                {
                    "url": "https://example.org/a",
                    "title": "A",
                    "publishedDate": "2026-09-10T08:00:00Z",
                    "highlights": ["first part", "second part"],
                },
                {"url": "https://example.org/b", "title": "B", "highlights": []},
            ],
            "costDollars": {"total": 0.007},
        }
        session = FakeSession(raw)
        profile = {"transport": "exa", "mode": "auto", "max_characters": 15}
        result = await exa.search(session, QUESTION, profile, "key")
        endpoint, request = session.calls[0]
        self.assertEqual(endpoint, "https://api.exa.ai/search")
        self.assertEqual(request["headers"], {"x-api-key": "key"})
        self.assertEqual(
            request["json"],
            {
                "query": QUESTION,
                "type": "auto",
                "numResults": 10,
                "contents": {"highlights": {"query": QUESTION, "maxCharacters": 15}},
            },
        )
        self.assertEqual(result["request"], request["json"])
        self.assertEqual(result["results"][0]["text"], "first part\nseco")
        self.assertEqual(result["results"][0]["published"], "2026-09-10")
        self.assertEqual(result["cost_usd"], 0.007)
        self.assertNotIn("error", result)

    async def test_http_failure_is_an_explicit_error(self):
        result = await exa.search(
            FakeSession({"error": "bad"}, 401),
            QUESTION,
            {"transport": "exa", "mode": "fast"},
            "key",
        )
        self.assertTrue(result["error"].startswith("HTTP_401"))
        self.assertEqual(result["results"], [])


class ParallelSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_question_is_sent_verbatim_as_objective_and_only_query(self):
        raw = {
            "results": [
                {
                    "url": "https://example.org/a",
                    "title": "A",
                    "publish_date": "2026-09-11",
                    "excerpts": ["one", "two"],
                }
            ],
            "usage": [{"name": "sku_search", "count": 1}],
        }
        session = FakeSession(raw)
        result = await parallel.search(
            session, QUESTION, {"transport": "parallel", "mode": "advanced"}, "key"
        )
        endpoint, request = session.calls[0]
        self.assertEqual(endpoint, "https://api.parallel.ai/v1/search")
        self.assertEqual(request["headers"], {"x-api-key": "key"})
        self.assertEqual(
            request["json"],
            {
                "objective": QUESTION,
                "search_queries": [QUESTION],
                "mode": "advanced",
                "advanced_settings": {
                    "max_results": 10,
                    "excerpt_settings": {"max_chars_per_result": 2000},
                },
            },
        )
        self.assertEqual(result["results"][0]["text"], "one\ntwo")
        self.assertIsNone(result["cost_usd"])
        self.assertEqual(result["usage"], raw["usage"])


if __name__ == "__main__":
    unittest.main()
