import unittest

from voicework.learning import build_profile, contextual_preferences, export_markdown, retrieve, validate_context


def sample(id_, channel="work", status="confirmed", held_out=False, **context):
    return {"id": id_, "title": "Example " + id_, "text": "Evidence unique " + id_, "status": status,
            "held_out": held_out, "context": validate_context({"channel": channel, **context}), "created_at": "2026-01-01"}


class LearningTests(unittest.TestCase):
    def test_retrieval_excludes_unconfirmed_holdout_and_other_channel(self):
        evidence = [sample("good"), sample("holdout", held_out=True), sample("personal", "personal"),
                    sample("pending", status="pending"), sample("assisted", status="assisted"), sample("rejected", status="rejected")]
        self.assertEqual([s["id"] for s in retrieve(evidence, {"channel": "work"})], ["good"])

    def test_context_match_ranks_more_relevant_evidence(self):
        evidence = [sample("other", audience="friend", intent="invite"), sample("matching", audience="client", intent="follow up")]
        self.assertEqual(retrieve(evidence, {"channel": "work", "audience": "client", "intent": "follow up"})[0]["id"], "matching")
        feedback = [{"id": "private", "context": {"channel": "personal"}}, {"id": "work", "context": {"channel": "work"}}]
        self.assertEqual([f["id"] for f in contextual_preferences(feedback, {"channel": "work"})], ["work"])

    def test_profile_distinguishes_training_coverage_and_contributions(self):
        profile = build_profile([sample("one"), sample("holdout", held_out=True), sample("pending", status="pending")], [])
        self.assertEqual((profile["confirmed_count"], profile["training_count"], profile["held_out_count"]), (2, 1, 1))
        self.assertEqual(profile["contexts"], [{"channel": "work", "count": 1}])
        self.assertEqual(profile["xp"], 40)
        self.assertTrue(all(p["evidence_count"] == 1 for p in profile["patterns"]))
        self.assertNotIn("accuracy", profile)

    def test_export_omits_samples_by_default_and_excludes_holdouts(self):
        items = [sample("train"), sample("holdout", held_out=True), sample("unreviewed", status="pending")]
        default = export_markdown(items, [])
        self.assertNotIn("Evidence unique train", default)
        included = export_markdown(items, [], True)
        self.assertIn("Evidence unique train", included)
        self.assertNotIn("Evidence unique holdout", included)
        self.assertNotIn("Evidence unique unreviewed", included)

    def test_empty_profile_does_not_fabricate_evidence(self):
        result = build_profile([], [])
        self.assertEqual(result["patterns"], [])
        self.assertEqual(result["preferences"], [])
        self.assertEqual(result["xp"], 0)

    def test_export_preserves_preference_contrast_and_pooling_limits(self):
        feedback = [{"context": {"channel": "personal"}, "choice": "a", "chosen_text": "Chosen generated option", "rejected_texts": ["Rejected generated option"]}]
        exported = export_markdown([], feedback)
        self.assertIn("Chosen generated option", exported)
        self.assertIn("Rejected generated option", exported)
        self.assertIn("Pooled descriptive", exported)


if __name__ == "__main__":
    unittest.main()
