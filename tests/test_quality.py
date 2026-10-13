import concurrent.futures
import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from voicework.corrections import CorrectionStore
from voicework.evaluation import EvaluationStore, NEUTRAL_CONTROLS
from voicework.language import LanguageLessonStore
from voicework.quality import QualityStore, REHEARSAL_WARNING
from voicework.store import Store


class RecordingProvider:
    def __init__(self, mode="codex", mutate=False):
        self.mode, self.model, self.calls, self.mutate = mode, "synthetic-test-model", [], mutate

    def generate_draft(self, context, brief, controls, evidence, preferences):
        self.calls.append(copy.deepcopy((context, brief, controls, evidence, preferences)))
        if self.mutate:
            context["channel"] = "mutated"
            controls["polish"] = 0
            if evidence:
                evidence[0]["text"] = "mutated"
            if preferences:
                preferences[0]["notes"] = "mutated"
        return {"provider": self.mode, "text": "Synthetic candidate %s." % len(self.calls),
                "warnings": ["Private candidate warning %s" % len(self.calls)]}


def rating(choice="a", meaning=None, effort=None, **extra):
    return {"choice": choice, "meaning": meaning or {"a": "preserved", "b": "preserved"},
            "effort": effort or {"a": "light", "b": "none"}, **extra}


class QualityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "voicework.sqlite3"
        self.training = Store(self.path)
        self.lessons = LanguageLessonStore(self.path)
        self.corrections = CorrectionStore(self.path)
        self.quality = QualityStore(self.path)
        self.context = {"channel": "work", "audience": "teammate", "scenario": "Give a schedule update."}
        saved = self.corrections.add({"kind": "style", "scope": "similar", "dimension": "diction",
                                      "context": self.context, "original_text": "We will utilize the checklist Friday.",
                                      "revised_text": "We will use the checklist Friday.", "guidance": "Prefer ordinary verbs.",
                                      "preferred": "use", "avoided": "utilize"})
        self.correction, self.lesson = saved["correction"], saved["lesson"]

    def tearDown(self):
        self.temp.cleanup()

    def create(self, provider=None, evidence=None, preferences=None, flips=(False, False)):
        with patch("voicework.quality.secrets.choice", side_effect=flips):
            return self.quality.create_case(self.correction, self.context, "The rehearsal starts Tuesday.",
                                            provider or RecordingProvider(), evidence or [], preferences or [], self.lesson)

    def test_same_current_inputs_only_target_lesson_differs(self):
        provider = RecordingProvider(mutate=True)
        evidence = [{"id": "approved", "text": "Synthetic authored reference.", "status": "confirmed", "context": self.context},
                    {"id": "heldout", "text": "Never supply this.", "status": "confirmed", "held_out": True},
                    {"id": "pending", "status": "pending"}, {"id": "assisted", "status": "assisted"},
                    {"id": "rejected", "status": "rejected"},
                    {"id": "other-channel", "status": "confirmed", "context": {"channel": "personal"}}]
        other_lesson = {**self.lesson, "id": "other-lesson", "guidance": "Keep short paragraphs.", "is_language_lesson": True}
        preferences = [{"id": "feedback", "context": self.context, "notes": "No signoff."}, other_lesson,
                       {**other_lesson, "id": "disabled", "enabled": False},
                       {"id": "unconfirmed-prior", "is_prior": True, "confirmed": False},
                       {"id": "prior", "is_prior": True, "confirmed": True, "text": "Context only.", "context": self.context},
                       {**self.lesson, "is_language_lesson": True}, dict(self.lesson)]
        originals = copy.deepcopy((evidence, preferences, self.context, self.lesson))
        self.create(provider, evidence, preferences)
        without, with_ = provider.calls
        self.assertEqual(without[:4], with_[:4])
        self.assertEqual(without[2], NEUTRAL_CONTROLS)
        self.assertEqual([item["id"] for item in without[3]], ["approved"])
        self.assertEqual([item["id"] for item in without[4]], ["feedback", "other-lesson", "prior"])
        self.assertEqual(with_[4][:-1], without[4])
        self.assertEqual(with_[4][-1]["id"], self.lesson["id"])
        self.assertTrue(with_[4][-1]["is_language_lesson"])
        self.assertEqual((evidence, preferences, self.context, self.lesson), originals)

    def test_generation_and_presentation_randomize_independently_and_stay_blind(self):
        for generation_flip in (False, True):
            for display_flip in (False, True):
                with self.subTest(generation=generation_flip, display=display_flip):
                    provider = RecordingProvider()
                    case = self.create(provider, flips=(generation_flip, display_flip))
                    self.assertEqual(bool(provider.calls[0][4]), generation_flip)
                    expected_a = "updated" if display_flip else "previous"
                    first_generated = "updated" if generation_flip else "previous"
                    expected_number = 1 if expected_a == first_generated else 2
                    self.assertEqual(case["options"][0]["text"], "Synthetic candidate %s." % expected_number)
                    self.assertNotIn("mapping", case)
                    self.assertNotIn("provenance", case)
                    self.assertNotIn("generation_order", case)
                    self.assertNotIn("Private candidate", json.dumps(case))
                    listed = next(item for item in QualityStore(self.path).list_cases() if item["id"] == case["id"])
                    self.assertEqual(listed, case)
                    rated = self.quality.rate_case(case["id"], rating())
                    self.assertEqual(rated["mapping"]["a"], expected_a)
                    self.assertEqual(rated["result"], expected_a)
                    self.assertIn("Private candidate", json.dumps(rated["warnings"]))

    def test_failed_second_generation_or_provider_change_saves_nothing(self):
        for failure in (ValueError("unavailable"), {"provider": "offline", "text": "silent fallback"},
                        {"provider": "codex", "text": "", "warnings": []},
                        {"provider": "codex", "text": "Synthetic", "warnings": [False]}):
            provider = RecordingProvider()
            with patch.object(provider, "generate_draft", side_effect=[{"provider": "codex", "text": "First"}, failure]):
                with self.assertRaises(ValueError):
                    self.create(provider)
        provider = RecordingProvider()
        def switch_model(*args):
            provider.model = "changed-model"
            return {"provider": "codex", "text": "Synthetic"}
        with patch.object(provider, "generate_draft", side_effect=switch_model), self.assertRaises(ValueError):
            self.create(provider)
        self.assertEqual(self.quality.list_cases(), [])

    def test_inactive_changed_or_mismatched_target_rejected_before_model(self):
        provider = RecordingProvider()
        for change in ({"enabled": False}, {"guidance": "Changed guidance"}, {"context": {"channel": "personal"}}):
            with self.subTest(change=change):
                self.lessons.update(self.lesson["id"], change)
                with self.assertRaises(ValueError):
                    self.create(provider)
                restore = {key: self.lesson[key] for key in ("context", "dimension", "guidance", "preferred", "avoided", "enabled")}
                self.lessons.update(self.lesson["id"], restore)
        for bad_target in ({**self.lesson, "enabled": False}, {**self.lesson, "id": "different"}):
            with self.assertRaises(ValueError):
                self.quality.create_case(self.correction, self.context, "Fresh brief", provider, [], [], bad_target)
        with self.assertRaises(ValueError):
            self.quality.create_case(self.correction, {"channel": "personal"}, "Fresh brief", provider, [], [], self.lesson)
        self.assertEqual(provider.calls, [])

    def test_mid_generation_edit_disable_delete_or_undo_prevents_persistence(self):
        for operation in ("edit", "disable", "delete", "undo"):
            with self.subTest(operation=operation):
                saved = self.corrections.add({"kind": "style", "scope": "channel", "dimension": "syntax", "context": self.context,
                                              "original_text": "A synthetic first version.", "revised_text": "A synthetic revised version.", "guidance": "Lead with the point."})
                provider = RecordingProvider()
                original_call = provider.generate_draft
                def change_after_generation(*args):
                    value = original_call(*args)
                    if len(provider.calls) == 2:
                        if operation == "edit":
                            self.lessons.update(saved["lesson"]["id"], {"guidance": "Changed rule."})
                        elif operation == "disable":
                            self.lessons.update(saved["lesson"]["id"], {"enabled": False})
                        elif operation == "delete":
                            self.lessons.delete(saved["lesson"]["id"])
                        else:
                            self.corrections.undo(saved["correction"]["id"])
                    return value
                with patch.object(provider, "generate_draft", side_effect=change_after_generation), self.assertRaises(ValueError):
                    self.quality.create_case(saved["correction"], self.context, "A fresh independent brief.", provider, [], [], saved["lesson"])
        self.assertEqual(self.quality.list_cases(), [])

    def test_provenance_frozen_without_raw_source_or_evidence_snapshots(self):
        evidence = [{"id": "source-id", "text": "Private synthetic evidence marker.", "status": "confirmed", "context": self.context}]
        preferences = [{"id": "feedback-id", "notes": "Private synthetic preference marker.", "context": self.context}]
        case = self.create(evidence=evidence, preferences=preferences)
        with sqlite3.connect(self.path) as conn:
            frozen = conn.execute("SELECT provenance FROM quality_cases WHERE id=?", (case["id"],)).fetchone()[0]
        for forbidden in (evidence[0]["text"], preferences[0]["notes"], self.correction["original_text"], self.correction["revised_text"]):
            self.assertNotIn(forbidden, frozen)
        evidence[0]["text"] = "Changed after generation."
        evidence[0]["held_out"] = True
        preferences[0]["notes"] = "Changed after generation."
        self.lessons.update(self.lesson["id"], {"enabled": False})
        rated = QualityStore(self.path).rate_case(case["id"], rating())
        self.assertEqual(rated["provenance"], json.loads(frozen))
        self.assertEqual(rated["provenance"]["lesson_fingerprint"], self.correction["lesson_fingerprint"])
        self.assertEqual(rated["provenance"]["evidence"][0]["id"], "source-id")
        self.assertEqual(len(rated["provenance"]["common_inputs_fingerprint"]), 64)
        self.assertEqual(rated["controls"], NEUTRAL_CONTROLS)

    def test_independent_meaning_effort_labels_required_and_ratings_immutable(self):
        case = self.create()
        bad = [{"choice": "a"}, rating(choice="bad"), rating(meaning={"a": "preserved"}),
               rating(meaning={"a": "preserved", "b": "good"}), rating(effort={"a": "none", "b": False}),
               rating(extra="unexpected"), rating(notes="x" * 5001)]
        for payload in bad:
            with self.assertRaises(ValueError):
                self.quality.rate_case(case["id"], payload)
        self.assertEqual(self.quality.list_cases()[0]["status"], "unrated")
        with self.assertRaises(KeyError):
            self.quality.rate_case("missing", rating())
        rated = self.quality.rate_case(case["id"], rating(choice="b", meaning={"a": "preserved", "b": "changed"},
                                                        effort={"a": "none", "b": "substantial"}, notes="Voice preference differs from meaning safety."))
        self.assertEqual(rated["result"], "updated")
        self.assertEqual(rated["observations"]["updated"], {"meaning": "changed", "effort": "substantial"})
        for choice in ("b", "tie"):
            with self.assertRaises(ValueError):
                self.quality.rate_case(case["id"], rating(choice))
        self.assertEqual(self.quality.list_cases()[0], rated)

    def test_concurrent_votes_record_exactly_once(self):
        case = self.create()
        def vote(_):
            try:
                self.quality.rate_case(case["id"], rating())
                return True
            except ValueError:
                return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sum(pool.map(vote, range(8))), 1)
        self.assertEqual(self.quality.summary()["rated_real"], 1)

    def test_summary_real_outcomes_are_separate_from_offline_and_meaning_effort(self):
        for choice in ("a", "b", "tie", "neither"):
            case = self.create()
            self.quality.rate_case(case["id"], rating(choice, meaning={"a": "unsure", "b": "changed"}))
        self.create()
        offline = self.create(RecordingProvider("offline"))
        self.assertIn(REHEARSAL_WARNING, offline["warnings"])
        self.quality.rate_case(offline["id"], rating("b", meaning={"a": "changed", "b": "changed"}))
        summary = self.quality.summary()
        self.assertEqual({key: summary[key] for key in ("total", "unrated", "rated_real", "previous_wins", "updated_wins", "ties", "neither", "meaning_issues", "offline_count", "rated_offline")},
                         {"total": 6, "unrated": 1, "rated_real": 4, "previous_wins": 1, "updated_wins": 1, "ties": 1, "neither": 1, "meaning_issues": 4, "offline_count": 1, "rated_offline": 1})
        self.assertEqual(summary["meaning"]["previous"]["unsure"], 4)
        self.assertEqual(summary["meaning"]["updated"]["changed"], 4)
        self.assertEqual(summary["effort"]["previous"]["light"], 4)
        self.assertEqual(summary["effort"]["updated"]["none"], 4)
        self.assertEqual(summary["per_correction"][0]["correction_id"], self.correction["id"])
        self.assertNotIn("accuracy", summary)

    def test_quality_ratings_never_enter_learning_or_legacy_evaluation(self):
        lesson_before = self.lessons.list()
        case = self.create()
        self.quality.rate_case(case["id"], rating(notes="A quality-check observation only."))
        self.assertEqual(self.training.samples(), [])
        self.assertEqual(self.training.feedback(), [])
        self.assertEqual(self.training.rounds(), [])
        self.assertEqual(self.lessons.list(), lesson_before)
        self.assertEqual(EvaluationStore(self.path).list_cases(), [])


if __name__ == "__main__":
    unittest.main()
