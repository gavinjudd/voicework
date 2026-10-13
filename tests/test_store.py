import concurrent.futures
import tempfile
import unittest
from pathlib import Path

from voicework.store import Store


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "data" / "voicework.sqlite3"
        self.store = Store(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_empty_and_review_persists(self):
        self.assertEqual(self.store.samples(), [])
        sample = self.store.add_sample({"title": "Hello", "text": "A reviewed sample"})
        self.assertEqual(sample["status"], "pending")
        self.assertFalse(sample["held_out"])
        self.store.update_sample(sample["id"], {"status": "confirmed", "held_out": True, "context": {"channel": "personal"}})
        actual = Store(self.path).samples()[0]
        self.assertEqual(actual["status"], "confirmed")
        self.assertEqual(actual["context"]["channel"], "personal")
        self.assertTrue(actual["held_out"])
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_atomic_import_and_duplicates(self):
        with self.assertRaises(ValueError):
            self.store.add_samples([{"text": "Keep no partial import"}, {"text": ""}])
        self.assertEqual(self.store.samples(), [])
        result = self.store.add_samples([{"text": "One  sample"}, {"text": "One sample"}])
        self.assertEqual((result["added"], result["duplicates"]), (1, 1))
        with self.assertRaises(ValueError):
            self.store.add_sample({"text": "No bypass", "status": "confirmed"})

    def test_concurrent_dedup_is_atomic(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.store.add_sample({"text": "Same concurrent sample"}), range(20)))
        self.assertEqual(len({s["id"] for s in results}), 1)
        self.assertEqual(len(self.store.samples()), 1)

    def test_feedback_keeps_contrast_and_is_not_sample(self):
        sample = self.store.add_sample({"text": "Real sample"})
        round_ = self.store.add_round({"channel": "personal"}, {
            "provider": "offline", "options": [{"id": "a", "text": "First"}, {"id": "b", "text": "Second"}]
        }, [sample["id"]])
        feedback = self.store.add_feedback({"round_id": round_["id"], "choice": "a", "edited_text": "My correction", "notes": "Shorter", "presentation": {"structure": "prose"}})
        self.assertEqual(feedback["chosen_text"], "First")
        self.assertEqual(feedback["rejected_texts"], ["Second"])
        self.assertEqual(feedback["context"]["channel"], "personal")
        self.assertEqual(len(self.store.samples()), 1)
        self.assertEqual(Store(self.path).rounds()[0]["status"], "completed")
        with self.assertRaises(ValueError):
            self.store.add_feedback({"round_id": round_["id"], "choice": "b"})
        self.store.delete_sample(sample["id"])
        self.assertEqual(self.store.samples(), [])
        self.assertEqual(self.store.rounds()[0]["evidence_ids"], [])

    def test_limits_and_unsafe_urls(self):
        for payload in ({"text": "x" * 30001}, {"text": 5}, {"text": "hello", "source_url": "javascript:alert(1)"}, {"text": "hello", "source_url": "https://u:pass@site.test"}):
            with self.subTest(payload=str(payload)[:70]), self.assertRaises(ValueError):
                self.store.add_sample(payload)
        sample = self.store.add_sample({"text": "Valid"})
        for patch in ({"held_out": "false"}, {"status": []}, {"context": {"channel": 2}}, {"text": "Cannot rewrite evidence"}):
            with self.assertRaises(ValueError):
                self.store.update_sample(sample["id"], patch)


if __name__ == "__main__":
    unittest.main()
