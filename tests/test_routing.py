import copy
import unittest

from voicework.corrections import lesson_fingerprint
from voicework.language import matching_lessons
from voicework.learning import contextual_preferences, retrieve, validate_context
from voicework.matching import (explain_context_match, explain_lesson_context_match,
                                normalize_label, same_channel)
from voicework.routing import resolve_context


def article_context(**changes):
    return {"channel": "long-form technical article", "audience": "technical readers",
            "intent": "explain an investigation and its implications", "relationship": "professional",
            "stakes": "medium", "scenario": "When evidence is preliminary, distinguish observation from inference.",
            **changes}


def lesson(ident, **changes):
    return {"id": ident, "enabled": True, "context": article_context(), "dimension": "diction",
            "guidance": "Use concrete wording and state uncertainty.", "preferred": "", "avoided": "",
            "created_at": "2026-01-01T00:00:00Z", **changes}


class TechnicalContextMatchingTests(unittest.TestCase):
    def test_fresh_synonyms_recover_all_lessons_and_keep_original_scope(self):
        lessons = [lesson("synthetic-%s" % index) for index in range(22)]
        original = copy.deepcopy(lessons)
        fingerprints = [lesson_fingerprint(item) for item in lessons]
        for channel, intent in (("technical article", "explain technical research"),
                                ("technical research article", "describe the research findings and their implications"),
                                ("technical explainer", "unpack a technical investigation"),
                                ("long form technical article", "clarify the technical findings")):
            with self.subTest(channel=channel, intent=intent):
                requested = article_context(channel=channel, intent=intent)
                self.assertEqual(matching_lessons(lessons, requested), lessons)
                explanation = explain_lesson_context_match(lessons[0]["context"], requested)
                self.assertEqual(explanation["reason"], "all_declared_conditions_match")
                self.assertEqual([item["field"] for item in explanation["alias_matches"]], ["channel", "intent"])
                self.assertTrue(explanation["scenario_is_conditional"])
        self.assertEqual(lessons, original)
        self.assertEqual([lesson_fingerprint(item) for item in lessons], fingerprints)

    def test_explanation_family_requires_explicit_technical_article_channel(self):
        for channel in ("work", "personal", "unknown", "legal article", "academic paper", "article"):
            with self.subTest(channel=channel):
                declared = article_context(channel=channel)
                requested = article_context(channel=channel, intent="explain technical research")
                self.assertFalse(explain_lesson_context_match(declared, requested)["eligible"])
        self.assertEqual(normalize_label("intent", "describe research"), "describe research")

    def test_qualifiers_negation_and_compound_goals_remain_literal(self):
        for intent in ("do not explain technical research", "explain technical research without implications",
                       "explain technical research only privately", "summarize technical research",
                       "explain technical research and request funding", "explain a criminal investigation",
                       "explain research methods", "explain technical research for beginners",
                       "explain technical research (provisional)"):
            with self.subTest(intent=intent):
                context = article_context(channel="technical article", intent=intent)
                self.assertEqual(normalize_label("intent", intent, context), intent)
                self.assertEqual(matching_lessons([lesson("scoped")], context), [])
        for channel in ("short technical article", "informal technical article", "private technical article",
                        "not a technical article", "technical article for children", "technical article or personal text"):
            self.assertFalse(same_channel(article_context(), article_context(channel=channel)))

    def test_each_declared_audience_relationship_and_stakes_stays_required(self):
        requested = article_context(channel="technical article", intent="explain technical research")
        for field, values in (("audience", ("general public", "unknown", "", "technical readers except clients")),
                              ("relationship", ("personal", "unknown", "")),
                              ("stakes", ("high", "unknown", ""))):
            for value in values:
                with self.subTest(field=field, value=value):
                    self.assertEqual(matching_lessons([lesson("scoped")], {**requested, field: value}), [])
        scoped = lesson("conditional")
        self.assertEqual(matching_lessons([scoped], {**requested, "scenario": "An unrelated example scenario."}), [scoped])
        self.assertIn("preliminary", scoped["context"]["scenario"])

    def test_authorship_holdout_and_disabled_boundaries_under_new_aliases(self):
        requested = article_context(channel="technical article", intent="explain technical research")
        sample = {"id": "eligible", "text": "Synthetic reference.", "context": article_context(),
                  "status": "confirmed", "held_out": False}
        samples = [sample] + [{**sample, "id": state, "status": state} for state in ("pending", "assisted", "rejected")]
        samples.append({**sample, "id": "held-out", "held_out": True})
        self.assertEqual(retrieve(samples, requested), [sample])
        self.assertEqual(contextual_preferences([{"context": article_context()}], requested), [{"context": article_context()}])
        self.assertEqual(matching_lessons([lesson("off", enabled=False)], requested), [])
        self.assertEqual(explain_context_match(sample["context"], requested)["alias_matches"][0]["canonical"], "technical article")

    def test_unknown_channels_abstain_for_all_evidence_types(self):
        for channel in ("unknown", "unspecified", "unclear", "n/a"):
            context = {"channel": channel}
            self.assertFalse(same_channel(context, context))
            self.assertFalse(explain_lesson_context_match(context, context)["eligible"])
            self.assertFalse(explain_context_match(context, context)["eligible"])

    def test_formal_informal_and_visibility_boundaries_block_strong_context(self):
        shared = {"channel": "work", "audience": "community", "relationship": "professional", "stakes": "low"}
        for formal, informal in (("formal status update", "casual status update"),
                                 ("formal follow-up", "informal follow up"),
                                 ("formal", "conversational")):
            found = explain_context_match({**shared, "intent": formal}, {**shared, "intent": informal})
            self.assertFalse(found["eligible"])
            self.assertEqual(found["reason"], "register_conflict")
        found = explain_context_match({**shared, "intent": "informal public announcement"},
                                      {**shared, "intent": "informal direct message"})
        self.assertFalse(found["eligible"])
        self.assertEqual(found["reason"], "visibility_conflict")
        found = explain_context_match(article_context(), article_context(intent="do not explain technical research"))
        self.assertFalse(found["eligible"])
        self.assertEqual(found["reason"], "intent_polarity_conflict")


class ContextResolutionTests(unittest.TestCase):
    def test_resolution_explains_aliases_and_never_overwrites_explicit_context(self):
        requested = article_context(channel="technical article", intent="explain technical research")
        available = [article_context()]
        original = copy.deepcopy((requested, available))
        result = resolve_context(requested, available, text="Write a casual personal text instead.")
        self.assertEqual(result["context"], validate_context(requested))
        self.assertEqual(result["requested_context"], result["context"])
        self.assertEqual(result["changes"], [])
        self.assertEqual(result["inference"], "none")
        self.assertEqual([item["field"] for item in result["matches"]], ["channel", "intent"])
        self.assertEqual(result["matches"][0]["available_labels"], ["long-form technical article"])
        self.assertIn("not inferred", result["warnings"][0])
        self.assertEqual(result["candidates"], [{"context": available[0], "reason": "matching_channel"}])
        self.assertEqual((requested, available), original)
        result["context"]["channel"] = "changed output"
        self.assertEqual(requested["channel"], "technical article")

    def test_ambiguity_unknown_and_unsupported_labels_never_choose_available_context(self):
        available = [article_context(), validate_context({"channel": "personal text"})]
        for channel in ("unknown", "article or text", "research", "technical memo"):
            requested = {"channel": channel}
            result = resolve_context(requested, available, "Explain a study and its implications.")
            self.assertEqual(result["context"]["channel"], channel)
            self.assertEqual(result["changes"], [])
            self.assertEqual(result["matches"], [])
            self.assertTrue(result["warnings"])
            self.assertTrue(all(item["reason"] == "available_channel" for item in result["candidates"]))

    def test_missing_details_warn_instead_of_relaxing_conditions(self):
        result = resolve_context({"channel": "technical article", "audience": "unknown"}, [article_context()])
        self.assertTrue(any("No available context matches" in warning for warning in result["warnings"]))
        self.assertEqual(result["context"]["audience"], "unknown")
        self.assertEqual(result["context"]["intent"], "")

    def test_manual_defaults_validation_and_bounded_candidates(self):
        self.assertEqual(resolve_context({})["context"], validate_context({}))
        for invalid in ({"channel": 1}, {"channel": "x" * 201}, {"recipient": "someone"}, []):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                resolve_context(invalid)
        with self.assertRaises(ValueError):
            resolve_context({}, text=None)
        available = [validate_context({"channel": "personal", "audience": "synthetic-%s" % number}) for number in range(20)]
        matching = validate_context({"channel": "work"})
        result = resolve_context({"channel": "work"}, available + [matching, matching])
        self.assertEqual(len(result["candidates"]), 12)
        self.assertEqual(result["candidates"][0], {"context": matching, "reason": "matching_channel"})


if __name__ == "__main__":
    unittest.main()
