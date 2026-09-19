import copy
import json
import unittest

from evaluators.passages import (
    MAX_SECTION_CHARS,
    PassageError,
    build_input,
    validate_reading,
)


def reading(label="answers", answer="30", section="text-1"):
    return {
        "label": label,
        "extracted_answer": answer,
        "highlights": [] if section is None else [section],
        "explanation": "The supplied passage establishes the count.",
    }


def comparison(label="equivalent"):
    return {"label": label, "explanation": "The requested count matches."}


class PassageTests(unittest.TestCase):
    def setUp(self):
        self.question = {
            "question": "How many bodies were recovered?",
            "answer": "30 bodies",
            "answer_aliases": ["thirty bodies"],
            "facts": [{"text": "hidden fact", "quote": "hidden gold quote"}],
            "reference_sources": [{"url": "https://secret-gold.example"}],
            "provider": "hidden question provider",
        }
        self.source = {
            "title": "Ferry fire update",
            "text": "Emergency update. Recovered 30 bodies. More details follow.",
            "url": "https://example.test/news",
            "published": "2026-09-11",
            "rank": 7,
            "provider": "hidden provider",
            "answer": "hidden injected answer",
        }
        self.inputs = build_input(self.question, self.source)

    def test_judge_input_hides_provider_rank_and_domain(self):
        serialized = json.dumps(self.inputs)
        for secret in (
            "hidden",
            "secret-gold",
            "thirty bodies",
            '"facts"',
            '"rank"',
            '"provider"',
            '"answer"',
        ):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, serialized)
        self.assertEqual(set(self.inputs), {"question", "source", "sections"})
        self.assertEqual(set(self.inputs["source"]), {"published"})
        self.assertNotIn("example", serialized)
        self.assertEqual(self.inputs["sections"][0]["field"], "title")

    def test_reader_preserves_every_unicode_character_and_whitespace_in_order(self):
        source = {
            "title": "  Café 😊\n",
            "text": "é😊\n\t" + "relevant text " * 500 + "\n\n Last.\t ",
        }
        actual = build_input("Question?", source)
        for field in ("title", "text"):
            sections = [s for s in actual["sections"] if s["field"] == field]
            self.assertEqual("".join(s["text"] for s in sections), source[field])
            previous = 0
            for section in sections:
                self.assertEqual(section["start"], previous)
                self.assertEqual(
                    source[field][section["start"] : section["end"]], section["text"]
                )
                previous = section["end"]
            self.assertEqual(previous, len(source[field]))
        self.assertIsNone(actual["source"]["published"])
        self.assertEqual(sum(s["field"] == "title" for s in actual["sections"]), 1)

    def test_sections_are_deterministic_bounded_and_do_not_change_numbers_or_names(
        self,
    ):
        source = {
            "text": "Context " * 54
            + "6,876.74 points for D’Arcy O’Neill.\n\n"
            + "More evidence. " * 50
            + "The exact count is 14,203."
        }
        first = build_input("Question?", source)
        self.assertEqual(
            first,
            build_input("Different question?", source) | {"question": "Question?"},
        )
        self.assertEqual("".join(s["text"] for s in first["sections"]), source["text"])
        self.assertGreater(len(first["sections"]), 2)
        for section in first["sections"]:
            self.assertLessEqual(len(section["text"]), MAX_SECTION_CHARS)
        for number in ("6,876.74", "14,203"):
            self.assertTrue(any(number in s["text"] for s in first["sections"]))
        self.assertIn("D’Arcy O’Neill", "".join(s["text"] for s in first["sections"]))

    def test_long_unbroken_token_is_preserved_without_inserting_characters(self):
        text = "x" * (MAX_SECTION_CHARS + 10)
        inputs = build_input("Question?", {"text": text})
        self.assertEqual(len(inputs["sections"]), 1)
        self.assertEqual(inputs["sections"][0]["text"], text)

    def test_selected_sections_get_original_unicode_offsets_and_exact_punctuation(self):
        text = "é😊 " + "Detail. " * 60 + "\n\nRecovered 30 bodies.\t More follows."
        inputs = build_input(self.question, {"text": text})
        selected = inputs["sections"][-1]
        result = validate_reading(reading(section=selected["id"]), inputs)
        highlight = result["highlights"][0]
        self.assertGreater(highlight["start"], 0)
        self.assertNotEqual(
            highlight["start"], len(text[: highlight["start"]].encode())
        )
        self.assertEqual(
            text[highlight["start"] : highlight["end"]], highlight["quote"]
        )
        self.assertEqual(highlight["quote"], selected["text"])
        self.assertIn("\t", highlight["quote"])

    def test_title_evidence_uses_the_provided_title_section(self):
        inputs = build_input(
            self.question, {"title": "Recovered 30 bodies.", "text": ""}
        )
        result = validate_reading(reading(section="title-1"), inputs)
        self.assertEqual(
            result["highlights"],
            [
                {
                    "field": "title",
                    "quote": "Recovered 30 bodies.",
                    "start": 0,
                    "end": 20,
                }
            ],
        )
        self.assertTrue(result["content_available"])

    def test_unknown_ids_copied_quotes_and_model_offsets_are_rejected(self):
        for selected in (
            "text-999",
            "title-2",
            "url-1",
            "Recovered 30 bodies.",
            {"field": "text", "quote": "Recovered 30 bodies."},
            {"id": "text-1", "start": 0},
            1,
            None,
        ):
            payload = reading()
            payload["highlights"] = [selected]
            with self.subTest(selected=selected), self.assertRaises(PassageError):
                validate_reading(payload, self.inputs)

    def test_repeated_text_can_be_selected_without_ambiguous_copy_errors(self):
        text = ("Recovered 30 bodies. " * 30) + "Other news."
        inputs = build_input(self.question, {"text": text})
        payload = reading()
        payload["highlights"] = [s["id"] for s in inputs["sections"]]
        result = validate_reading(payload, inputs)
        self.assertEqual("".join(h["quote"] for h in result["highlights"]), text)
        self.assertEqual(len(result["highlights"]), len(inputs["sections"]))

    def test_duplicate_section_ids_are_rejected(self):
        payload = reading()
        payload["highlights"] *= 2
        with self.assertRaisesRegex(PassageError, "Repeated"):
            validate_reading(payload, self.inputs)

    def test_selected_context_and_answer_sections_keep_their_original_fields(self):
        source = {
            "title": "September 2026 ferry fire",
            "text": "Location and attribution. " * 20 + "\n\nRecovered 30 bodies.",
        }
        inputs = build_input(self.question, source)
        payload = reading()
        payload["highlights"] = ["title-1", "text-1", inputs["sections"][-1]["id"]]
        result = validate_reading(payload, inputs)
        self.assertEqual(result["highlights"][0]["field"], "title")
        for highlight in result["highlights"]:
            self.assertEqual(
                source[highlight["field"]][highlight["start"] : highlight["end"]],
                highlight["quote"],
            )
        self.assertIn("Recovered 30 bodies.", result["highlights"][-1]["quote"])

    def test_corrupted_section_order_offsets_and_ids_are_rejected(self):
        mutations = [
            lambda items: items[1].update(start=1),
            lambda items: items[1].update(end=999),
            lambda items: items[1].update(id="text-2"),
            lambda items: items.reverse(),
            lambda items: items[1].update(field="url"),
        ]
        for mutate in mutations:
            inputs = copy.deepcopy(self.inputs)
            mutate(inputs["sections"])
            with self.subTest(mutate=mutate), self.assertRaises(PassageError):
                validate_reading(reading(), inputs)

    def test_complete_partial_absent_and_uncertain_contracts(self):
        valid = [
            reading(),
            reading("partial", "30"),
            reading("partial", None),
            reading("no_answer", None, section=None),
            reading("uncertain", None),
        ]
        for payload in valid:
            with self.subTest(label=payload["label"]):
                result = validate_reading(payload, self.inputs)
                self.assertEqual(result["label"], payload["label"])
        invalid = [
            reading("answers", None),
            reading("answers", section=None),
            reading("partial", section=None),
            reading("no_answer", "30", section=None),
            reading("no_answer", None),
            reading("uncertain", "30"),
        ]
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(PassageError):
                validate_reading(payload, self.inputs)
