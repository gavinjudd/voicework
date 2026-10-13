import copy
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from voicework.corrections import CorrectionConflict, CorrectionStore, lesson_fingerprint, validate_correction
from voicework.language import LanguageLessonStore, matching_lessons
from voicework.store import Store


def packet(**overrides):
    return {"original_text": "We will utilize the checklist tomorrow.", "revised_text": "We will use the checklist tomorrow.",
            "guidance": "Use plain everyday verbs without changing the facts.", "preferred": "use", "avoided": "utilize",
            "context": {"channel": "work", "audience": "client", "intent": "update"}, "scope": "similar", "kind": "style", **overrides}


class CorrectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "voicework.sqlite3"
        self.samples = Store(self.path)
        self.lessons = LanguageLessonStore(self.path)
        self.corrections = CorrectionStore(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_atomic_save_keeps_source_provenance_separate_from_authored_samples(self):
        result = self.corrections.add(packet())
        correction, lesson = result["correction"], result["lesson"]
        self.assertEqual(CorrectionStore(self.path).get(correction["id"]), correction)
        self.assertEqual(correction["lesson_id"], lesson["id"])
        self.assertEqual(correction["lesson_snapshot"], lesson)
        self.assertEqual(correction["lesson_fingerprint"], lesson_fingerprint(lesson))
        self.assertEqual(correction["original_text"], packet()["original_text"])
        self.assertEqual(correction["status"], "active")
        self.assertEqual(self.samples.samples(), [])
        self.assertEqual(self.samples.feedback(), [])
        self.assertEqual(self.lessons.list(), [lesson])

    def test_failed_correction_insert_rolls_back_its_lesson(self):
        with sqlite3.connect(str(self.path)) as conn:
            conn.execute("CREATE TRIGGER fail_correction BEFORE INSERT ON corrections BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.corrections.add(packet())
        self.assertEqual(self.lessons.list(), [])
        self.assertEqual(self.corrections.list(), [])

    def test_scope_requires_conditions_and_never_silently_broadens(self):
        with self.assertRaisesRegex(ValueError, "Similar situations"):
            validate_correction(packet(context={"channel": "work", "scenario": "Only a scenario is not a scope condition"}))
        lesson = self.corrections.add(packet())["lesson"]
        self.assertEqual(matching_lessons([lesson], {"channel": "work", "audience": "teammate", "intent": "update"}), [])
        self.assertEqual(matching_lessons([lesson], {"channel": "work", "audience": "CLIENT", "intent": "UPDATE"}), [lesson])
        value = self.corrections.add(packet(scope="channel"))["correction"]
        self.assertTrue(all(not text for key, text in value["context"].items() if key != "channel"))
        self.assertEqual(value["source_context"]["audience"], "client")

    def test_fact_edits_unchanged_drafts_bad_types_and_limits_are_rejected(self):
        for value in (packet(kind="facts"), packet(kind=None), packet(revised_text=packet()["original_text"]),
                      packet(guidance=" "), packet(original_text="x" * 30001), packet(preferred="x" * 501),
                      packet(avoided=[]), packet(scope=[]), packet(extra="no")):
            with self.subTest(keys=list(value)), self.assertRaises(ValueError):
                self.corrections.add(value)
        self.assertEqual(self.lessons.list(), [])

    def test_undo_only_disables_own_lesson_and_is_idempotent(self):
        first = self.corrections.add(packet())
        later = self.corrections.add(packet(guidance="A later independent preference", preferred="start", avoided="commence"))
        undone = self.corrections.undo(first["correction"]["id"])
        self.assertEqual(undone["correction"]["status"], "undone")
        self.assertFalse(undone["lesson"]["enabled"])
        self.assertTrue(self.corrections.require_active(later["correction"]["id"])[1]["enabled"])
        self.assertEqual(self.corrections.undo(first["correction"]["id"]), undone)
        # A repeated undo must not revert a later explicit re-enable or edit.
        modified = self.lessons.update(first["lesson"]["id"], {"enabled": True, "guidance": "Later explicit edit"})
        again = self.corrections.undo(first["correction"]["id"])
        self.assertEqual(again["lesson"], modified)

    def test_undo_detects_changed_or_deleted_targets_without_mutating_them(self):
        saved = self.corrections.add(packet())
        changed = self.lessons.update(saved["lesson"]["id"], {"preferred": "choose"})
        self.assertEqual(self.corrections.get(saved["correction"]["id"])["status"], "lesson_changed")
        with self.assertRaises(CorrectionConflict):
            self.corrections.undo(saved["correction"]["id"])
        self.assertEqual(self.lessons.list(), [changed])
        self.lessons.delete(changed["id"])
        self.assertEqual(self.corrections.get(saved["correction"]["id"])["status"], "lesson_deleted")
        with self.assertRaises(CorrectionConflict):
            self.corrections.undo(saved["correction"]["id"])

    def test_checks_reject_unmatched_scope_source_reuse_and_inactive_corrections(self):
        saved = self.corrections.add(packet())
        ident = saved["correction"]["id"]
        context = packet()["context"]
        self.corrections.prepare_check(ident, context, "Another brief on the same topic.")
        for source in (packet()["original_text"], packet()["revised_text"].upper()):
            with self.assertRaisesRegex(ValueError, "different brief"):
                self.corrections.prepare_check(ident, context, source)
        with self.assertRaisesRegex(ValueError, "declared context"):
            self.corrections.prepare_check(ident, {"channel": "personal"}, "A new brief")
        self.lessons.update(saved["lesson"]["id"], {"enabled": False})
        with self.assertRaises(CorrectionConflict):
            self.corrections.prepare_check(ident, context, "A new brief")


if __name__ == "__main__":
    unittest.main()
