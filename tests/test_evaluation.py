import copy
import concurrent.futures
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from voicework.evaluation import EvaluationStore, NEUTRAL_CONTROLS, REHEARSAL_WARNING
from voicework.store import Store


class RecordingProvider:
    def __init__(self, mode="codex"):
        self.mode, self.model, self.calls = mode, "test-model", []

    def generate_draft(self, context, brief, controls, evidence, preferences):
        self.calls.append(copy.deepcopy((context, brief, controls, evidence, preferences)))
        label = "Candidate one" if len(self.calls) % 2 else "Candidate two"
        return {"text": label, "provider": self.mode, "warnings": ["private candidate warning %s" % len(self.calls)]}


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "data" / "voicework.sqlite3"
        self.store = EvaluationStore(self.path)
        self.context = {"channel": "work", "audience": "teammate", "scenario": "Describe the confirmed schedule."}

    def tearDown(self):
        self.temp.cleanup()

    def create(self, provider=None, flipped=False, context=None):
        with patch("voicework.evaluation.secrets.choice", return_value=flipped):
            return self.store.create_case(context or self.context, "Launch is Friday.", provider or RecordingProvider(), [], [])

    def test_same_task_controls_and_filtered_evidence(self):
        provider = RecordingProvider()
        evidence = [{"id": "approved", "text": "Reviewed style.", "status": "confirmed", "context": self.context},
                    {"id": "holdout", "text": "Never supply this.", "status": "confirmed", "held_out": True},
                    {"id": "pending", "status": "pending"}, {"id": "assisted", "status": "assisted"},
                    {"id": "rejected", "status": "rejected"}]
        preferences = [{"notes": "Use short paragraphs."}]
        self.store.create_case(self.context, "Launch is Friday.", provider, evidence, preferences)
        baseline, calibrated = provider.calls
        self.assertEqual(baseline[:3], calibrated[:3])
        self.assertEqual(baseline[2], NEUTRAL_CONTROLS)
        self.assertEqual(baseline[3:], ([], []))
        self.assertEqual([s["id"] for s in calibrated[3]], ["approved"])
        self.assertEqual(calibrated[4], preferences)
        self.assertEqual(len(evidence), 5)

    def test_blind_mapping_and_candidate_warnings_until_rating(self):
        case = self.create(flipped=True)
        self.assertEqual(case["options"][0], {"id": "a", "text": "Candidate two"})
        self.assertNotIn("mapping", case)
        self.assertNotIn("private candidate", " ".join(case["warnings"]))
        self.assertNotIn("mapping", EvaluationStore(self.path).list_cases()[0])
        rated = self.store.rate_case(case["id"], "a", False, "This is easier to send.")
        self.assertEqual(rated["mapping"], {"a": "calibrated", "b": "baseline"})
        self.assertEqual(rated["result"], "calibrated")
        self.assertEqual(rated["status"], "rated")
        self.assertIn("private candidate", " ".join(rated["warnings"]))
        self.assertEqual(EvaluationStore(self.path).list_cases()[0], rated)

    def test_both_randomized_orders_are_revealed_correctly(self):
        for flipped, winner in ((False, "baseline"), (True, "calibrated")):
            with self.subTest(flipped=flipped):
                case = self.create(flipped=flipped)
                self.assertEqual(self.store.rate_case(case["id"], "a")["result"], winner)

    def test_summary_counts_choices_and_excludes_offline(self):
        for choice, flagged in (("a", False), ("b", True), ("tie", False), ("neither", False)):
            case = self.create()
            self.store.rate_case(case["id"], choice, flagged)
        self.create()
        rehearsal = self.create(RecordingProvider("offline"), context={"channel": "personal"})
        self.assertIn(REHEARSAL_WARNING, rehearsal["warnings"])
        self.store.rate_case(rehearsal["id"], "a", True)
        summary = self.store.summary()
        self.assertEqual({k: summary[k] for k in ("total", "unrated", "rated_real", "calibrated_wins", "baseline_wins", "ties", "neither", "factual_flags", "offline_count", "rated_offline")},
                         {"total": 6, "unrated": 1, "rated_real": 4, "calibrated_wins": 1, "baseline_wins": 1, "ties": 1, "neither": 1, "factual_flags": 1, "offline_count": 1, "rated_offline": 1})
        contexts = {item["channel"]: item for item in summary["per_context"]}
        self.assertEqual(contexts["personal"]["rated_real"], 0)
        self.assertEqual(contexts["work"]["rated_real"], 4)
        self.assertNotIn("accuracy", summary)

    def test_invalid_or_duplicate_ratings_leave_case_unchanged(self):
        case = self.create()
        for args in (("invalid", False, ""), ([], False, ""), ("a", 1, ""), ("a", False, "x" * 5001)):
            with self.subTest(args=str(args)[:40]), self.assertRaises(ValueError):
                self.store.rate_case(case["id"], *args)
        self.assertEqual(self.store.list_cases()[0]["status"], "unrated")
        with self.assertRaises(KeyError):
            self.store.rate_case("missing", "a")
        rated = self.store.rate_case(case["id"], "tie")
        for choice in ("tie", "a"):
            with self.assertRaises(ValueError):
                self.store.rate_case(case["id"], choice)
        self.assertEqual(self.store.list_cases()[0], rated)

    def test_concurrent_votes_record_exactly_once(self):
        case = self.create()
        def vote(_):
            try:
                self.store.rate_case(case["id"], "a")
                return True
            except ValueError:
                return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sum(pool.map(vote, range(8))), 1)
        self.assertEqual(self.store.summary()["rated_real"], 1)

    def test_failure_or_provider_switch_never_saves_partial_case(self):
        provider = RecordingProvider()
        with patch.object(provider, "generate_draft", side_effect=[{"provider": "codex", "text": "First"}, ValueError("Unavailable")]):
            with self.assertRaises(ValueError):
                self.create(provider)
        with patch.object(provider, "generate_draft", return_value={"provider": "offline", "text": "Fallback"}):
            with self.assertRaises(ValueError):
                self.create(provider)
        self.assertEqual(self.store.list_cases(), [])

    def test_evaluation_never_becomes_training_feedback(self):
        training_store = Store(self.path)
        case = self.create()
        self.store.rate_case(case["id"], "b", notes="Evaluation only")
        self.assertEqual(training_store.feedback(), [])
        self.assertEqual(training_store.samples(), [])
        self.assertEqual(training_store.rounds(), [])


if __name__ == "__main__":
    unittest.main()
