import copy
import unittest

from voicework.language import matching_lessons
from voicework.learning import contextual_preferences, retrieve
from voicework.matching import (ALIAS_GROUPS, explain_context_match,
                                lesson_context_matches, normalize_label, same_channel)


def record(ident, **context):
    return {"id": ident, "context": {"channel": "work", **context},
            "status": "confirmed", "held_out": False, "created_at": "2026-01-01"}


class LabelMatchingTests(unittest.TestCase):
    def test_every_alias_is_explicit_and_field_specific(self):
        for field, groups in ALIAS_GROUPS.items():
            for group in groups:
                for alias in group:
                    with self.subTest(field=field, alias=alias):
                        self.assertEqual(normalize_label(field, "  " + alias.upper() + "  "), group[0])
        self.assertNotEqual(normalize_label("audience", "moderate"), normalize_label("audience", "medium"))
        self.assertNotEqual(normalize_label("channel", "coworker"), normalize_label("channel", "colleague"))

    def test_unknown_and_qualified_labels_remain_literal(self):
        labels = ("prospective client", "former client", "client (provisional)",
                  "not a client", "client except renewals", "private client", "Acme client",
                  "partner", "close friend", "routine but confidential", "low, not public")
        for label in labels:
            with self.subTest(label=label):
                self.assertEqual(normalize_label("audience", label), label.casefold())
                self.assertEqual(normalize_label("stakes", label), label.casefold())
        self.assertEqual(normalize_label("audience", "  Straße   Group "), "strasse group")
        self.assertEqual(normalize_label("audience", "unknown"), "unknown")
        self.assertNotEqual(normalize_label("audience", "unknown"), normalize_label("audience", ""))

    def test_channels_are_hard_boundaries(self):
        self.assertTrue(same_channel({"channel": " WORK "}, {"channel": "work"}))
        self.assertTrue(same_channel({"channel": "Forum   Chat"}, {"channel": "forum chat"}))
        for left, right in (("work", "personal"), ("work", "professional"), ("public", "private"), ("work", "unknown")):
            with self.subTest(left=left, right=right):
                self.assertFalse(same_channel({"channel": left}, {"channel": right}))

    def test_lessons_require_all_declared_conditions_with_safe_aliases(self):
        declared = {"channel": "work", "audience": "coworker", "intent": "ask for a review",
                    "relationship": "colleague", "stakes": "low-stakes",
                    "scenario": "Only for private requests before the review deadline."}
        requested = {"channel": " Work ", "audience": "colleague", "intent": "request review",
                     "relationship": "coworker", "stakes": "low",
                     "scenario": "A differently worded situation"}
        lesson = {"id": "synthetic", "enabled": True, "context": declared}
        before = copy.deepcopy(lesson)
        self.assertEqual(matching_lessons([lesson], requested), [lesson])
        self.assertEqual(lesson, before)
        self.assertIn("private", lesson["context"]["scenario"])
        for field in ("audience", "intent", "relationship", "stakes"):
            for replacement in ("", "unknown", "different"):
                with self.subTest(field=field, replacement=replacement):
                    self.assertFalse(lesson_context_matches(declared, {**requested, field: replacement}))

    def test_only_blank_lesson_conditions_are_unrestricted(self):
        request = {"channel": "work", "audience": "client"}
        self.assertTrue(lesson_context_matches({"channel": "work", "audience": "  "}, request))
        self.assertFalse(lesson_context_matches({"channel": "work", "audience": "unknown"}, request))
        self.assertFalse(lesson_context_matches({"channel": "work", "audience": "custom circle"}, request))
        self.assertTrue(lesson_context_matches({"channel": "work", "audience": "Custom Circle"},
                                              {"channel": "work", "audience": " custom   circle "}))
        self.assertTrue(lesson_context_matches({"channel": "work", "audience": "unknown"},
                                              {"channel": "work", "audience": "unknown"}))

    def test_qualified_and_compound_lesson_conditions_do_not_broaden(self):
        for declared, requested in (("close friend", "friend"), ("former client", "client"),
                                    ("not urgent", "urgent"), ("public reply", "reply"),
                                    ("request review only privately", "request review"),
                                    ("acknowledge and invite elaboration", "invite elaboration")):
            with self.subTest(declared=declared):
                self.assertFalse(lesson_context_matches({"channel": "work", "intent": declared},
                                                       {"channel": "work", "intent": requested}))
        self.assertEqual(matching_lessons([{"enabled": False, "context": {"channel": "work"}}], {"channel": "work"}), [])


class EvidenceMatchingTests(unittest.TestCase):
    def test_useful_alias_match_retrieves_without_modifying_sources(self):
        candidate = record("useful", audience="colleague", intent="request review", relationship="coworker", stakes="low")
        request = {"channel": "WORK", "audience": "coworker", "intent": "ask for review", "relationship": "colleague", "stakes": "low stakes"}
        before = copy.deepcopy(candidate)
        match = explain_context_match(candidate["context"], request)
        self.assertTrue(match["eligible"])
        self.assertEqual(match["matched_fields"], ["audience", "intent", "relationship", "stakes"])
        self.assertEqual(match["score"], 16)
        self.assertEqual(retrieve([candidate], request), [candidate])
        self.assertEqual(contextual_preferences([candidate], request), [candidate])
        self.assertEqual(candidate, before)

    def test_conflicting_audience_relationship_and_stakes_reject(self):
        request = {"channel": "work", "audience": "client", "relationship": "professional", "stakes": "high", "intent": "status update"}
        base = record("base", **{key: value for key, value in request.items() if key != "channel"})
        for field, conflicting in (("audience", "team"), ("audience", "AI assistant"),
                                   ("relationship", "close friend"), ("stakes", "low")):
            with self.subTest(field=field, conflicting=conflicting):
                candidate = {**base, "context": {**base["context"], field: conflicting}}
                self.assertEqual(retrieve([candidate], request), [])
                self.assertEqual(contextual_preferences([candidate], request), [])
                self.assertEqual(explain_context_match(candidate["context"], request)["reason"], "context_conflict")

    def test_public_private_and_work_personal_boundaries(self):
        candidate = record("public", audience="community", intent="public announcement", relationship="professional")
        self.assertEqual(retrieve([candidate], {**candidate["context"], "intent": "direct message"}), [])
        self.assertEqual(retrieve([candidate], {**candidate["context"], "channel": "personal"}), [])
        for public in ("public announcement", "public post", "public reply", "public update", "public message"):
            for private in ("private message", "private reply", "private update", "private note", "private conversation"):
                with self.subTest(public=public, private=private):
                    actual = {**candidate["context"], "intent": private}
                    requested = {**candidate["context"], "intent": public}
                    self.assertEqual(explain_context_match(actual, requested)["reason"], "visibility_conflict")
                    self.assertFalse(explain_context_match(actual, requested)["eligible"])
        self.assertEqual(retrieve([record("private", audience="private group")], {"channel": "work", "audience": "public group"}), [])
        for left, right in (("partner", "romantic partner"), ("partner", "business partner"),
                            ("close friend", "friend"), ("former client", "client")):
            self.assertEqual(retrieve([record("qualified", audience=left)], {"channel": "work", "audience": right}), [])

    def test_contextless_fallback_only_for_truly_channel_only_query(self):
        candidate = record("same-channel", audience="client", intent="apology")
        self.assertEqual(retrieve([candidate], {"channel": "work"}), [candidate])
        self.assertEqual(explain_context_match(candidate["context"], {"channel": "work"})["reason"], "channel_only_fallback")
        for detail in ({"audience": "unknown"}, {"scenario": "Write a short message please."},
                       {"stakes": "low"}, {"intent": "unknown"}, {"relationship": "unspecified"}):
            self.assertEqual(retrieve([record("bare")], {"channel": "work", **detail}), [])

    def test_unknown_labels_never_supply_relevance(self):
        self.assertEqual(retrieve([record("unknown", audience="unknown")], {"channel": "work", "audience": "unknown"}), [])
        unknown_channel = {**record("unknown-channel"), "context": {"channel": "unknown"}}
        self.assertEqual(retrieve([unknown_channel], {"channel": "unknown"}), [])
        self.assertEqual(retrieve([record("custom", audience="Internal Circle Seven")],
                                  {"channel": "work", "audience": "internal circle seven"})[0]["id"], "custom")

    def test_missing_metadata_does_not_conflict_but_requires_other_relevance(self):
        candidate = record("partial", audience="unknown", intent="request review")
        self.assertEqual(retrieve([candidate], {"channel": "work", "audience": "colleague", "intent": "ask for review"}), [candidate])
        self.assertEqual(retrieve([candidate], {"channel": "work", "audience": "colleague"}), [])

    def test_shared_stakes_alone_are_not_sufficient(self):
        for stakes in ("routine", "low", "high"):
            self.assertEqual(retrieve([record("generic", stakes=stakes)], {"channel": "work", "stakes": stakes}), [])

    def test_scenario_requires_informative_overlap_not_generic_words(self):
        candidate = record("topical", scenario="Check the launch rehearsal checklist.")
        request = {"channel": "work", "scenario": "Ask about the launch rehearsal checklist for tomorrow."}
        self.assertEqual(retrieve([candidate], request), [candidate])
        self.assertEqual(explain_context_match(candidate["context"], request)["scenario_overlap"], ["checklist", "launch", "rehearsal"])
        cases = (("Write a short update for the team today.", "Send a short update for the team tomorrow."),
                 ("Discuss the launch budget.", "Discuss the lunch menu."),
                 ("A checklist for launch rehearsal.", "A checklist for a dinner reservation."),
                 ("alpha beta gamma delta epsilon", "alpha beta zeta eta theta"))
        for actual, wanted in cases:
            with self.subTest(actual=actual, wanted=wanted):
                self.assertEqual(retrieve([record("irrelevant", scenario=actual)], {"channel": "work", "scenario": wanted}), [])

    def test_scenario_negation_cannot_supply_opposite_polarity_keyword_match(self):
        candidate = record("positive", scenario="Approve the launch rehearsal.")
        self.assertEqual(retrieve([candidate], {"channel": "work", "scenario": "Do not approve the launch rehearsal."}), [])
        candidate = record("contraction", scenario="Don't approve the launch rehearsal.")
        self.assertEqual(retrieve([candidate], {"channel": "work", "scenario": "Approve the launch rehearsal."}), [])

    def test_ranking_threshold_and_limits(self):
        request = {"channel": "work", "audience": "client", "relationship": "professional", "intent": "request review", "stakes": "low"}
        candidates = [record("channel-only"), record("stakes-only", stakes="low"),
                      record("audience", audience="client"),
                      record("full", audience="customer", relationship="professional", intent="ask for review", stakes="low-stakes"),
                      record("relationship", relationship="professional")]
        self.assertEqual([item["id"] for item in retrieve(candidates, request)], ["full", "audience", "relationship"])
        self.assertEqual([item["id"] for item in retrieve(candidates, request, limit=2)], ["full", "audience"])
        self.assertEqual([item["id"] for item in contextual_preferences(candidates, request, limit=2)], ["full", "audience"])

    def test_intent_disagreement_needs_stronger_context_and_preserves_scope(self):
        request = {"channel": "work", "audience": "client", "relationship": "professional", "intent": "invitation"}
        stronger = record("same-contact", audience="client", relationship="professional", intent="status update")
        weaker = record("only-audience", audience="client", intent="status update")
        self.assertEqual(retrieve([stronger, weaker], request), [stronger])
        self.assertEqual(explain_context_match(stronger["context"], request)["conflicting_fields"], ["intent"])
        self.assertEqual(stronger["context"]["intent"], "status update")

    def test_authorship_holdout_and_disabled_lesson_boundaries_still_apply(self):
        candidates = [record("eligible", audience="colleague")]
        for status in ("pending", "assisted", "rejected"):
            candidates.append({**record(status, audience="coworker"), "status": status})
        candidates.append({**record("held-out", audience="colleague"), "held_out": True})
        self.assertEqual([item["id"] for item in retrieve(candidates, {"channel": "work", "audience": "coworker"})], ["eligible"])


if __name__ == "__main__":
    unittest.main()
