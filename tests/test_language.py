import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_server
from voicework.evaluation import EvaluationStore
from voicework.language import LanguageLessonStore, matching_lessons, validate_lesson
from voicework.learning import build_profile, export_markdown, validate_context
from voicework.provider import Provider, _payload, _prompt
from voicework.store import Store


def packet(**overrides):
    return {"context": {"channel": "work"}, "dimension": "diction", "guidance": "Use plain everyday verbs; keep the facts.",
            "preferred": "use", "avoided": "utilize", **overrides}


class LanguageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "voicework.sqlite3"
        self.store = Store(self.path)
        self.lessons = LanguageLessonStore(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_direct_teaching_persists_without_samples_or_confirmation(self):
        lesson = self.lessons.add(packet())
        self.assertTrue(lesson["enabled"])
        self.assertEqual(LanguageLessonStore(self.path).list(), [lesson])
        self.assertEqual(self.store.samples(), [])
        profile = build_profile([], [], [lesson])
        self.assertEqual(profile["confirmed_count"], 0)
        self.assertEqual(profile["feedback_count"], 0)
        self.assertEqual(profile["language_lesson_count"], 1)
        self.assertEqual(profile["language_contexts"], [{"channel": "work", "count": 1}])
        self.assertEqual(profile["xp"], 10)
        self.assertEqual(profile["patterns"], [])
        self.assertNotIn("accuracy", profile)

    def test_exact_context_boundaries_casefold_and_descriptive_scenario(self):
        declared = {"channel": "Work", "audience": "Straße", "intent": "update", "relationship": "client", "stakes": "high", "scenario": "Only when summarizing project status"}
        lesson = self.lessons.add(packet(context=declared))
        requested = {**declared, "channel": " work ", "audience": "STRASSE", "scenario": "A different request wording"}
        self.assertEqual(self.lessons.matching(requested), [lesson])
        for field in ("channel", "audience", "intent", "relationship", "stakes"):
            with self.subTest(field=field):
                self.assertEqual(self.lessons.matching({**requested, field: "different"}), [])
                if field != "channel":
                    self.assertEqual(self.lessons.matching({**requested, field: ""}), [])
        wildcard = self.lessons.add(packet())
        self.assertEqual(self.lessons.matching({"channel": "work", "audience": "Other"}), [wildcard])

    def test_revoke_edit_delete_exclude_lessons_from_retrieval_profile_export(self):
        lesson = self.lessons.add(packet())
        self.assertIn("use", export_markdown([], [], language_lessons=[lesson]))
        edited = self.lessons.update(lesson["id"], {"guidance": "A distinct direct rule", "preferred": "", "avoided": "", "dimension": "syntax"})
        self.assertEqual(edited["created_at"], lesson["created_at"])
        self.assertEqual(edited["dimension"], "syntax")
        self.lessons.update(lesson["id"], {"enabled": False})
        self.assertEqual(self.lessons.matching({"channel": "work"}), [])
        self.assertEqual(build_profile([], [], self.lessons.list())["language_lesson_count"], 0)
        self.assertNotIn("A distinct direct rule", export_markdown([], [], language_lessons=self.lessons.list()))
        self.lessons.update(lesson["id"], {"enabled": True})
        self.assertIn("A distinct direct rule", export_markdown([], [], language_lessons=self.lessons.list()))
        self.lessons.delete(lesson["id"])
        self.assertEqual(self.lessons.list(), [])
        self.assertEqual(build_profile([], [], self.lessons.list())["xp"], 0)

    def test_invalid_lessons_and_partial_edits_are_non_destructive(self):
        invalid = [packet(dimension="tone"), packet(dimension=[]), packet(enabled=True),
                   packet(guidance="", preferred="  ", avoided=""), packet(guidance="x" * 2001),
                   packet(preferred="x" * 501), packet(avoided=12), packet(context={"mystery": "x"})]
        for value in invalid:
            with self.subTest(value=list(value)), self.assertRaises(ValueError):
                validate_lesson(value)
        lesson = self.lessons.add(packet())
        for invalid_patch in ({"enabled": "false"}, {"guidance": "", "preferred": "", "avoided": ""}, {"id": "replace"}, {}):
            with self.assertRaises(ValueError):
                self.lessons.update(lesson["id"], invalid_patch)
        self.assertEqual(self.lessons.list(), [lesson])

    def test_provider_payload_separates_enabled_applicable_lessons_from_evidence(self):
        active = {**self.lessons.add(packet()), "is_language_lesson": True}
        disabled = {**active, "enabled": False, "id": "disabled"}
        other = {**active, "context": {"channel": "personal"}, "id": "other"}
        scoped = {**active, "context": {"channel": "work", "audience": "client"}, "id": "scoped"}
        payload = _payload({"channel": "work"}, [], [active, disabled, other, scoped])
        self.assertEqual([item["id"] for item in payload["language_lessons"]], [active["id"]])
        self.assertEqual(payload["authored_examples"], [])
        self.assertEqual(payload["calibration_feedback"], [])
        self.assertEqual(payload["assistant_derived_priors"], [])
        prompt = _prompt("draft", payload)
        self.assertIn("not blind find-and-replace", prompt)
        self.assertIn("do not infer identity or use stereotypes", prompt)

    def test_calibrated_evaluation_only_gets_direct_lessons(self):
        lesson = {**self.lessons.add(packet()), "is_language_lesson": True}
        with patch.object(Provider, "_invoke", return_value={"text": "The checklist is ready.", "warnings": []}) as invoke:
            EvaluationStore(self.path).create_case({"channel": "work"}, "The checklist is ready.", Provider("codex"), [], [lesson])
        baseline, calibrated = [call.args[1] for call in invoke.call_args_list]
        self.assertEqual(baseline["language_lessons"], [])
        self.assertEqual(calibrated["language_lessons"][0]["id"], lesson["id"])
        self.assertEqual(baseline["brief"], calibrated["brief"])
        self.assertEqual(self.store.feedback(), [])

    def test_focused_round_prompt_persistence_and_feedback(self):
        lesson = {**self.lessons.add(packet()), "is_language_lesson": True}
        output = {"options": [{"id": "a", "text": "Use the checklist."}, {"id": "b", "text": "Work from the checklist."}], "warnings": []}
        with patch.object(Provider, "_invoke", return_value=output) as invoke:
            generated = Provider("codex").generate_round({"channel": "work"}, [], [lesson], focus="diction")
        self.assertEqual(invoke.call_args.args[1]["focus"], "diction")
        self.assertEqual(invoke.call_args.args[1]["language_lessons"][0]["preferred"], "use")
        round_ = self.store.add_round({"channel": "work"}, generated, [], focus="diction")
        self.assertEqual(Store(self.path).rounds()[0]["focus"], "diction")
        feedback = self.store.add_feedback({"round_id": round_["id"], "choice": "a"})
        self.assertEqual(feedback["focus"], "diction")
        self.assertIn("Practice focus: diction", export_markdown([], [feedback]))
        self.assertEqual(build_profile([], [feedback])["preferences"][0]["focus"], "diction")

    def test_legacy_rounds_migrate_to_mixed_without_losing_content(self):
        legacy_path = Path(self.temp.name) / "legacy.sqlite3"
        with sqlite3.connect(str(legacy_path)) as conn:
            conn.execute("CREATE TABLE rounds (id TEXT PRIMARY KEY,context TEXT,options TEXT,provider TEXT,warnings TEXT,evidence_ids TEXT,status TEXT,created_at TEXT)")
            conn.execute("INSERT INTO rounds VALUES (?,?,?,?,?,?,?,?)", ("legacy", "{}", '[{"id":"a","text":"Original"},{"id":"b","text":"Other"}]', "offline", "[]", "[]", "open", "2026-01-01"))
        migrated = Store(legacy_path).rounds()[0]
        self.assertEqual(migrated["focus"], "mixed")
        self.assertEqual(migrated["options"][0]["text"], "Original")

    def test_offline_focus_is_explicit_and_keeps_supplied_fact(self):
        for focus in ("vernacular", "syntax", "diction", "rhythm"):
            output = Provider().generate_round({"scenario": "The checklist is ready. Review starts Friday."}, [], [], focus=focus)
            for option in output["options"]:
                self.assertIn("The checklist is ready.", option["text"])
                self.assertIn("Review starts Friday.", option["text"])
            self.assertNotEqual(output["options"][0]["text"], output["options"][1]["text"])
            self.assertTrue(any("heuristic" in warning for warning in output["warnings"]))
        with self.assertRaises(ValueError):
            Provider().generate_round({}, [], [], focus="unknown")


class LanguageApiTests(unittest.TestCase):
    setUp = test_server.ServerTests.setUp
    tearDown = test_server.ServerTests.tearDown
    request = test_server.ServerTests.request

    def test_zero_sample_direct_teaching_crud_and_draft_retrieval(self):
        status, response, _ = self.request("POST", "/api/language-lessons", packet())
        self.assertEqual(status, 201)
        ident = response["lesson"]["id"]
        state = self.request("GET", "/api/state")[1]
        self.assertEqual(state["samples"], [])
        self.assertEqual(state["priors"], [])
        self.assertEqual(state["profile"]["language_lesson_count"], 1)
        self.assertEqual(len(state["language_lessons"]), 1)
        self.assertIn("Use plain everyday verbs", self.request("GET", "/api/export")[1]["markdown"])
        self.provider.mode = "codex"
        with patch.object(self.provider, "_invoke", return_value={"text": "A synthetic draft.", "warnings": []}) as invoke:
            self.assertEqual(self.request("POST", "/api/draft", {"context": {"channel": "work"}, "brief": "We will utilize the checklist tomorrow."})[0], 200)
            self.assertEqual(invoke.call_args.args[1]["language_lessons"][0]["preferred"], "use")
            self.assertEqual(self.request("PATCH", "/api/language-lessons/" + ident, {"enabled": False})[0], 200)
            self.request("POST", "/api/draft", {"brief": "A brief"})
            self.assertEqual(invoke.call_args.args[1]["language_lessons"], [])
        self.assertEqual(self.request("GET", "/api/state")[1]["profile"]["language_lesson_count"], 0)
        self.assertNotIn("Use plain everyday verbs", self.request("GET", "/api/export")[1]["markdown"])
        self.assertEqual(self.request("PATCH", "/api/language-lessons/" + ident, {"enabled": True, "preferred": "start"})[0], 200)
        self.assertEqual(self.request("DELETE", "/api/language-lessons/" + ident)[0], 200)
        self.assertEqual(self.request("GET", "/api/state")[1]["language_lessons"], [])

    def test_round_focus_and_invalid_api_payloads(self):
        status, response, _ = self.request("POST", "/api/rounds", {"focus": "syntax", "context": {"channel": "work"}})
        self.assertEqual(status, 201)
        self.assertEqual(response["round"]["focus"], "syntax")
        feedback = self.request("POST", "/api/feedback", {"round_id": response["round"]["id"], "choice": "a"})[1]["feedback"]
        self.assertEqual(feedback["focus"], "syntax")
        self.assertEqual(self.request("POST", "/api/rounds", {"focus": "unknown"})[0], 400)
        for invalid in (packet(dimension="unknown"), packet(confirmed=True), packet(guidance="", preferred="", avoided="")):
            self.assertEqual(self.request("POST", "/api/language-lessons", invalid)[0], 400)
        self.assertEqual(self.request("POST", "/api/language-lessons", packet(), {"X-Voicework-Token": "wrong"})[0], 403)
        self.assertEqual(self.request("PATCH", "/api/language-lessons/missing", {"enabled": False})[0], 404)


if __name__ == "__main__":
    unittest.main()
