"""Synthetic coverage for deliberate, one-way long-form teaching."""
import copy
import tempfile
import unittest
from pathlib import Path

from voicework.corrections import CorrectionStore
from voicework.language import LanguageLessonStore, matching_lessons
from voicework.learning import contextual_preferences, retrieve
from voicework.matching import LONGFORM_LESSON_CHANNELS, explain_lesson_context_match
from voicework.provider import _payload, _prompt
from voicework.routing import resolve_context


def broad(**changes):
    return {"id": "synthetic-broad", "enabled": True,
            "context": {"channel": "long-form writing"}, "dimension": "syntax",
            "guidance": "Connect related observations when readable.",
            "preferred": "", "avoided": "", **changes}


class LongformLessonTests(unittest.TestCase):
    def test_declared_scope_applies_only_to_supported_nonfiction_formats(self):
        item = broad()
        original = copy.deepcopy(item)
        for channel in LONGFORM_LESSON_CHANNELS | {"technical research article", "long form writing"}:
            with self.subTest(channel=channel):
                self.assertEqual(matching_lessons([item], {"channel": channel}), [item])
        for channel in ("work", "personal", "work email", "personal text", "public writing",
                        "unknown", "academic paper", "legal article", "fiction", "poetry",
                        "short technical article", "private essay", "not an essay", "essay or text"):
            with self.subTest(channel=channel):
                self.assertEqual(matching_lessons([item], {"channel": channel}), [])
        self.assertEqual(item, original)

    def test_narrower_conditions_and_disabled_state_remain_required(self):
        context = {"channel": "long-form writing", "audience": "technical readers",
                   "intent": "explain a design", "relationship": "professional", "stakes": "low"}
        item = broad(context=context)
        request = {**context, "channel": "project case study"}
        self.assertEqual(matching_lessons([item], request), [item])
        for field in ("audience", "intent", "relationship", "stakes"):
            for value in ("", "unknown", "different"):
                self.assertEqual(matching_lessons([item], {**request, field: value}), [])
        self.assertEqual(matching_lessons([{**item, "enabled": False}], request), [])

    def test_inheritance_is_one_way_and_never_imports_other_evidence(self):
        request = {"channel": "reflective essay"}
        sample = {"id": "synthetic-sample", "status": "confirmed", "held_out": False,
                  "text": "Synthetic observation.", "context": broad()["context"]}
        self.assertEqual(retrieve([sample], request), [])
        self.assertEqual(contextual_preferences([sample], request), [])
        specific = broad(id="specific", context={"channel": "technical article"})
        self.assertEqual(matching_lessons([specific], request), [])
        self.assertEqual(matching_lessons([specific], {"channel": "long-form writing"}), [])
        prior = {"is_prior": True, "confirmed": True, "context": broad()["context"]}
        payload = _payload(request, [sample], [prior, {**broad(), "is_language_lesson": True}])
        self.assertEqual(payload["authored_examples"], [])
        self.assertEqual(payload["assistant_derived_priors"], [])
        self.assertEqual([x["id"] for x in payload["language_lessons"]], ["synthetic-broad"])

    def test_routing_distinguishes_broad_lessons_from_channel_equivalence(self):
        context = broad()["context"]
        request = {"channel": "project case study"}
        report = resolve_context(request, [context], lesson_contexts=[context])
        self.assertEqual(report["context"]["channel"], request["channel"])
        self.assertEqual(report["changes"], [])
        self.assertEqual(report["warnings"], [])
        self.assertEqual(report["matches"][0]["reason"], "explicit_longform_lesson_scope")
        self.assertEqual(report["candidates"][0]["reason"], "longform_lesson_scope")
        explanation = explain_lesson_context_match(context, request)
        self.assertTrue(explanation["eligible"])
        self.assertEqual(explanation["alias_matches"][0]["reason"], "explicit_longform_lesson_scope")
        no_lesson = resolve_context(request, [context])
        self.assertEqual(no_lesson["matches"], [])
        self.assertTrue(no_lesson["warnings"])

    def test_undo_removes_general_lesson_without_touching_specific_preferences(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.sqlite3"
            lessons = LanguageLessonStore(path)
            corrections = CorrectionStore(path)
            narrow = lessons.add({"context": {"channel": "technical article"},
                                  "dimension": "syntax", "guidance": "Retain the qualification."})
            saved = corrections.add({"original_text": "The probe ran. It returned a value.",
                                     "revised_text": "The probe ran and returned a value.",
                                     "guidance": "Connect related actions when readable.",
                                     "context": {"channel": "long-form writing"},
                                     "dimension": "syntax", "scope": "channel", "kind": "style"})
            self.assertEqual(len(lessons.matching({"channel": "technical article"})), 2)
            corrections.undo(saved["correction"]["id"])
            self.assertEqual(lessons.matching({"channel": "technical article"}), [narrow])

    def test_prompt_prioritizes_specific_preferences_and_register(self):
        prompt = _prompt("document", {})
        self.assertIn("Prefer an applicable specific lesson over a broad default", prompt)
        self.assertIn("current brief and explicit register still govern", prompt)


class PersonalWebsiteTests(unittest.TestCase):
    """The writer's own site gets broad long-form defaults, never texting evidence."""

    def test_site_aliases_share_one_channel_and_inherit_broad_lessons(self):
        item = broad()
        for channel in ("personal website", "Personal Site", "personal website copy",
                        "portfolio website", "portfolio site"):
            with self.subTest(channel=channel):
                self.assertEqual(matching_lessons([item], {"channel": channel}), [item])
        for channel in ("website", "company website", "website copy", "site copy", "marketing site"):
            with self.subTest(channel=channel):
                self.assertEqual(matching_lessons([item], {"channel": channel}), [])

    def test_personal_channel_lessons_and_samples_stay_out(self):
        request = {"channel": "personal website", "audience": "people who follow me",
                   "intent": "introduce myself"}
        texting = broad(id="texting", context={"channel": "personal"})
        self.assertEqual(matching_lessons([texting], request), [])
        sample = {"id": "text", "status": "confirmed", "held_out": False,
                  "text": "Synthetic text to a friend.", "context": {"channel": "personal"}}
        self.assertEqual(retrieve([sample], request), [])
        site_lesson = broad(id="site", context={"channel": "personal website"})
        self.assertEqual(matching_lessons([site_lesson, texting], {**request, "channel": "portfolio site"}),
                         [site_lesson])
        prior = {"is_prior": True, "confirmed": True, "context": {"channel": "personal"}}
        payload = _payload(request, [sample], [prior])
        self.assertEqual(payload["assistant_derived_priors"], [])


if __name__ == "__main__":
    unittest.main()
