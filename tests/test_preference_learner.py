import copy
import json
import math
import random
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from voicework.preference_learner import PreferenceLearner, estimate_evidence
from voicework.store import Store
from voicework.corrections import CorrectionStore
from voicework.quality import QualityStore
from voicework.evaluation import EvaluationStore
from voicework.provider import Provider


CONTEXT = {"channel": "long-form writing"}


def trait_packet(**changes):
    return {"label": "Concrete explanations", "description": "Explain a mechanism with concrete details when useful.",
            "dimension": "diction", "context": CONTEXT, **changes}


def evidence_packet(**changes):
    return {"source_kind": "direct", "value": .7, "reliability": 1,
            "attribution": "I like the concrete explanation in this context.", "confirmed": True, **changes}


def observation(value=1, group="one", source="direct", reliability=1):
    return {"status": "confirmed", "eligible": True, "value": value,
            "group_key": group, "source_kind": source, "reliability": reliability}


class EstimatorTests(unittest.TestCase):
    def test_strength_and_confidence_are_separate(self):
        one_strong = estimate_evidence([observation()])
        many_mild = estimate_evidence([observation(.2, str(i)) for i in range(15)])
        self.assertEqual(one_strong["strength"], 1)
        self.assertEqual(many_mild["strength"], .2)
        self.assertGreater(many_mild["confidence"], one_strong["confidence"])
        self.assertIn("not a probability", many_mild["notice"])

    def test_five_comments_do_not_become_five_independent_confirmations(self):
        single = estimate_evidence([observation(.6, "video", "reference")])
        batch = estimate_evidence([observation(.6, "video", "reference") for _ in range(5)])
        self.assertEqual(single["confidence"], batch["confidence"])
        self.assertEqual(batch["independent_groups"], 1)
        self.assertEqual(batch["effective_mass"], .35)
        self.assertFalse(batch["applies"])

    def test_contradictory_evidence_is_not_latest_wins(self):
        first = [observation(1, "a"), observation(-1, "b")]
        result = estimate_evidence(first)
        self.assertEqual(result, estimate_evidence(list(reversed(first))))
        self.assertEqual(result["strength"], 0)
        self.assertEqual(result["confidence"], 0)
        self.assertTrue(result["disputed"])
        self.assertFalse(result["applies"])

    def test_disagreement_inside_one_correlated_batch_remains_visible(self):
        result = estimate_evidence([observation(1, "video", "reference"), observation(-1, "video", "reference")])
        self.assertEqual(result["independent_groups"], 1)
        self.assertTrue(result["disputed"])
        self.assertEqual(result["confidence"], 0)

    def test_one_weak_counterexample_does_not_erase_many_consistent_edits(self):
        rows = [observation(1, str(i), "edit") for i in range(10)]
        rows.append(observation(-1, "reference", "reference", .5))
        result = estimate_evidence(rows)
        self.assertGreater(result["strength"], .9)
        self.assertTrue(result["applies"])
        self.assertEqual(result["opposing_groups"], 1)

    def test_pending_retracted_and_ineligible_evidence_never_changes_an_estimate(self):
        rows = [observation(.4)]
        expected = estimate_evidence(rows)
        extra = [{**observation(-1, "pending"), "status": "pending"},
                 {**observation(-1, "retracted"), "status": "retracted"},
                 {**observation(-1, "invalid"), "eligible": False}]
        result = estimate_evidence(rows + extra)
        for key in ("strength", "confidence", "effective_mass", "influence", "independent_groups"):
            self.assertEqual(expected[key], result[key])
        self.assertEqual(result["pending_evidence"], 1)
        self.assertEqual(result["inactive_sources"], 1)

    def test_repeated_zero_strength_can_support_indifference_without_applying_a_trait(self):
        result = estimate_evidence([observation(0, str(i)) for i in range(12)])
        self.assertGreater(result["confidence"], .7)
        self.assertEqual(result["strength"], 0)
        self.assertFalse(result["applies"])

    def test_source_weights_and_reliability_affect_confidence_not_requested_strength(self):
        direct = estimate_evidence([observation(.8)])
        indirect = estimate_evidence([observation(.8, source="evaluation", reliability=.5)])
        self.assertEqual(direct["strength"], indirect["strength"])
        self.assertGreater(direct["confidence"], indirect["confidence"])

    def test_order_invariance_and_bounds_over_varied_evidence(self):
        rng = random.Random(51)
        for _ in range(100):
            rows = [observation(rng.uniform(-1, 1), str(rng.randrange(5)), reliability=rng.random()) for _ in range(20)]
            first = estimate_evidence(rows)
            rng.shuffle(rows)
            second = estimate_evidence(rows)
            for field in ("strength", "confidence", "effective_mass", "consistency", "influence"):
                self.assertAlmostEqual(first[field], second[field], places=5)
            self.assertTrue(-1 <= first["strength"] <= 1)
            for field in ("confidence", "consistency", "influence"):
                self.assertTrue(0 <= first[field] <= 1)


class _LearnerFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "voicework.sqlite3"
        self.store = Store(self.path)
        self.learner = PreferenceLearner(self.path)
        self.trait = self.learner.create_trait(trait_packet())

    def tearDown(self):
        self.temp.cleanup()


class LearnerStoreTests(_LearnerFixture, unittest.TestCase):
    def test_withdrawn_interpretation_can_be_revised_without_rewriting_history(self):
        source = self.store.add_sample({'text': 'A fictional liked passage.', 'context': CONTEXT})
        original = self.learner.add_evidence(self.trait['id'], evidence_packet(source_kind='reference', source_id=source['id']))
        revised_packet = evidence_packet(source_kind='reference', source_id=source['id'], value=-.4, attribution='I reconsidered which aspect I liked.')
        with self.assertRaises(ValueError):
            self.learner.add_evidence(self.trait['id'], revised_packet)
        self.learner.review_evidence(original['id'], 'retract', 'The original attribution was wrong')
        revised = self.learner.add_evidence(self.trait['id'], revised_packet)
        self.assertNotEqual(original['id'], revised['id'])
        self.assertEqual(self.learner.add_evidence(self.trait['id'], revised_packet)['id'], revised['id'])
        trait = self.learner.get_trait(self.trait['id'])
        self.assertEqual(trait['total_evidence'], 2)
        self.assertEqual(trait['estimate']['strength'], -.4)
        self.assertEqual(trait['estimate']['independent_groups'], 1)
        self.assertTrue(any(original['id'] in event['reason'] for event in trait['history']))

    def test_later_holdout_selection_withdraws_pasted_evidence(self):
        text = 'A fictional paragraph long enough to establish its distinctive meaning and exact source.'
        source = self.store.add_sample({'text': text, 'context': CONTEXT})
        self.learner.add_evidence(self.trait['id'], evidence_packet(source_kind='reference', reference_text=text))
        self.store.update_sample(source['id'], {'held_out': True})
        result = self.learner.get_trait(self.trait['id'])
        self.assertEqual(result['estimate']['reviewed_evidence'], 0)
        self.assertFalse(result['evidence'][0]['eligible'])

    def test_youtube_short_mobile_and_standard_links_share_the_same_source(self):
        for url in ('https://youtu.be/fixture?t=1', 'http://m.youtube.com/watch?v=fixture&lc=2', 'https://www.youtube.com/embed/fixture'):
            self.learner.add_evidence(self.trait['id'], evidence_packet(source_kind='reference', reference_text='A fictional comment at ' + url, source_url=url))
        self.assertEqual(self.learner.get_trait(self.trait['id'])['estimate']['independent_groups'], 1)

    def test_reinterpreting_an_unlinked_passage_does_not_add_independent_mass(self):
        for attribution in ('I like the directness.', 'I like the clarity.'):
            self.learner.add_evidence(self.trait['id'], evidence_packet(source_kind='reference', reference_text='One fictional passage.', attribution=attribution))
        self.assertEqual(self.learner.get_trait(self.trait['id'])['estimate']['effective_mass'], .35)
        for context in ({}, None, [], False, 0):
            with self.assertRaises(ValueError):
                self.learner.add_evidence(self.trait['id'], evidence_packet(context=context))

    def test_saved_reference_passage_is_reviewable_but_unsafe_imported_link_is_never_exposed(self):
        sample = self.store.add_sample({'text': 'A fictional reviewable source.', 'context': CONTEXT})
        with sqlite3.connect(str(self.path)) as conn:
            conn.execute("UPDATE samples SET source_url='javascript:alert(1)' WHERE id=?", (sample['id'],))
        event = self.learner.add_evidence(self.trait['id'], evidence_packet(source_kind='reference', source_id=sample['id']))
        self.assertEqual(event['source_url'], '')
        self.assertEqual(event['reference_text'], sample['text'])
        self.assertNotIn(sample['text'], json.dumps(self.learner.resolve(CONTEXT)))

    def test_additive_schema_and_no_authored_sample_or_legacy_lesson_mutation(self):
        from voicework.language import LanguageLessonStore
        lesson = LanguageLessonStore(self.path).add({"dimension": "diction", "guidance": "A synthetic old preference", "context": CONTEXT})
        sample = self.store.add_sample({"text": "A fictional authored sample.", "context": CONTEXT})
        self.store.update_sample(sample["id"], {"status": "confirmed"})
        before = self.store.samples(), LanguageLessonStore(self.path).list()
        self.learner.add_evidence(self.trait["id"], evidence_packet())
        self.assertEqual(before, (self.store.samples(), LanguageLessonStore(self.path).list()))
        self.assertEqual(PreferenceLearner(self.path).get_trait(self.trait["id"])["estimate"]["strength"], .7)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_ambiguous_liked_passage_remains_pending_until_reviewed(self):
        evidence = self.learner.add_evidence(self.trait["id"], evidence_packet(
            source_kind="reference", reference_text="It took four pots to use the entire bag of compost.",
            source_url="https://example.test/greenhouse", confirmed=False))
        self.assertEqual(evidence["status"], "pending")
        self.assertEqual(self.learner.get_trait(self.trait["id"])["estimate"]["strength"], 0)
        self.learner.review_evidence(evidence["id"], "confirm", "I like its concrete explanation, not every aspect")
        result = self.learner.get_trait(self.trait["id"])
        self.assertEqual(result["estimate"]["strength"], .7)
        self.assertEqual(self.store.samples(), [])

    def test_youtube_comments_share_a_group_even_with_different_comment_ids(self):
        for index in range(5):
            self.learner.add_evidence(self.trait["id"], evidence_packet(
                source_kind="reference", reference_text="Fictional comment number %d" % index,
                source_url="https://www.youtube.com/watch?v=fixture&lc=%d&utm_source=test" % index,
                attribution="This concrete explanation has useful specifics %d" % index))
        estimate = self.learner.get_trait(self.trait["id"])["estimate"]
        self.assertEqual(estimate["reviewed_evidence"], 5)
        self.assertEqual(estimate["independent_groups"], 1)
        self.assertEqual(estimate["effective_mass"], .35)

    def test_duplicate_clicks_are_idempotent_and_do_not_confirm_pending_evidence(self):
        packet = evidence_packet(confirmed=False)
        first = self.learner.add_evidence(self.trait["id"], packet)
        second = self.learner.add_evidence(self.trait["id"], {**packet, "confirmed": True})
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(second["status"], "pending")
        self.assertEqual(self.learner.get_trait(self.trait["id"])["total_evidence"], 1)

    def test_concurrent_duplicate_submissions_write_once(self):
        with ThreadPoolExecutor(max_workers=5) as pool:
            rows = list(pool.map(lambda _: self.learner.add_evidence(self.trait["id"], evidence_packet()), range(10)))
        self.assertEqual(len({row["id"] for row in rows}), 1)

    def test_invalid_numeric_values_and_unknown_fields_are_atomic(self):
        packets = [evidence_packet(value=x) for x in [True, float('nan'), float('inf'), -1.1, "1"]]
        packets += [evidence_packet(reliability=x) for x in [False, 0, -1, 1.1, float('nan')]]
        packets += [evidence_packet(confirmed=1), evidence_packet(extra="bad"), evidence_packet(attribution="")]
        for packet in packets:
            with self.subTest(packet=packet), self.assertRaises(ValueError):
                self.learner.add_evidence(self.trait["id"], packet)
        self.assertEqual(self.learner.get_trait(self.trait["id"])["total_evidence"], 0)

    def test_no_unknown_channel_defaults_and_no_scope_expansion(self):
        for context in [None, {}, {"audience": "friend"}, {"channel": ""}]:
            with self.assertRaises(ValueError):
                self.learner.create_trait(trait_packet(context=context))
        with self.assertRaisesRegex(ValueError, "declared context"):
            self.learner.add_evidence(self.trait["id"], evidence_packet(context={"channel": "personal"}))

    def test_longform_aliases_match_without_leaking_to_messages(self):
        self.learner.add_evidence(self.trait["id"], evidence_packet())
        for channel in ["long-form writing", "technical article", "long-form technical article", "personal website"]:
            self.assertEqual(len(self.learner.guidance({"channel": channel})), 1)
        for channel in ["personal", "work email", "academic paper", "unknown"]:
            self.assertEqual(self.learner.guidance({"channel": channel}), [])

    def test_contextual_evidence_stays_in_its_narrow_context(self):
        trait = self.learner.create_trait(trait_packet(context={"channel": "work"}))
        context = {"channel": "work", "audience": "client", "intent": "update"}
        self.learner.add_evidence(trait["id"], evidence_packet(context=context))
        self.assertEqual(self.learner.get_trait(trait["id"])["estimate"]["reviewed_evidence"], 0)
        self.assertEqual(self.learner.get_trait(trait["id"], context)["estimate"]["reviewed_evidence"], 1)
        self.assertEqual(self.learner.guidance({"channel": "work", "audience": "friend", "intent": "update"}), [])

    def test_conflicts_retract_restore_and_history_are_persistent(self):
        positive = self.learner.add_evidence(self.trait["id"], evidence_packet(value=1))
        negative = self.learner.add_evidence(self.trait["id"], evidence_packet(value=-1, attribution="In this different example I dislike the concrete detail"))
        self.assertTrue(self.learner.get_trait(self.trait["id"])["estimate"]["disputed"])
        self.assertEqual(self.learner.guidance(CONTEXT), [])
        self.assertIn("conflicting", self.learner.resolve(CONTEXT)["warnings"][0])
        self.learner.review_evidence(negative["id"], "retract", "I had mistaken a factual change for a style preference")
        self.assertEqual(self.learner.get_trait(self.trait["id"])["estimate"]["strength"], 1)
        self.assertEqual(len(self.learner.guidance(CONTEXT)), 1)
        self.learner.review_evidence(negative["id"], "restore", "I have reviewed it again and want it included")
        result = PreferenceLearner(self.path).get_trait(self.trait["id"])
        self.assertEqual(len(result["evidence"]), 2)
        self.assertEqual(len(result["history"]), 4)
        self.assertTrue(result["estimate"]["disputed"])

    def test_deactivation_retains_the_estimate_but_stops_generation(self):
        self.learner.add_evidence(self.trait["id"], evidence_packet())
        self.learner.update_trait(self.trait["id"], {"enabled": False})
        result = self.learner.get_trait(self.trait["id"])
        self.assertEqual(result["estimate"]["strength"], .7)
        self.assertFalse(result["estimate"]["applies"])
        self.assertEqual(self.learner.guidance(CONTEXT), [])
        self.learner.update_trait(self.trait["id"], {"enabled": True})
        self.assertEqual(len(self.learner.guidance(CONTEXT)), 1)

    def test_meaning_and_scope_are_not_rewritten_under_existing_evidence(self):
        for patch in [{"description": "An opposite meaning"}, {"context": {"channel": "personal"}}, {"enabled": 1}]:
            with self.assertRaises(ValueError):
                self.learner.update_trait(self.trait["id"], patch)

    def test_edit_requires_style_attribution_and_meaning_confirmation(self):
        packet = evidence_packet(source_kind="edit", before_text="Please utilize the diagram.", after_text="Please use the diagram.")
        with self.assertRaisesRegex(ValueError, "preserves meaning"):
            self.learner.add_evidence(self.trait["id"], packet)
        self.learner.add_evidence(self.trait["id"], {**packet, "style_only": True, "meaning_preserved": True})
        self.assertEqual(self.store.samples(), [])

    def test_raw_passages_never_enter_resolved_guidance(self):
        marker = "SECRET-FICTIONAL-PASSAGE-NOT-FOR-PROMPTS"
        self.learner.add_evidence(self.trait["id"], evidence_packet(source_kind="edit", before_text=marker, after_text="Short fictional replacement", style_only=True, meaning_preserved=True))
        self.assertNotIn(marker, json.dumps(self.learner.guidance(CONTEXT)))
        self.assertNotIn(marker, json.dumps(self.learner.resolve(CONTEXT)))
        self.assertIn(marker, json.dumps(self.learner.get_trait(self.trait["id"])))

    def test_bad_source_urls_and_oversize_text_do_not_persist(self):
        for url in ["file:///tmp/local", "https://user:secret@example.test/", "https://example.test:bad/"]:
            with self.assertRaises(ValueError):
                self.learner.add_evidence(self.trait["id"], evidence_packet(source_kind="reference", reference_text="test", source_url=url))
        with self.assertRaises(ValueError):
            self.learner.add_evidence(self.trait["id"], evidence_packet(source_kind="reference", reference_text="x" * 4001))

    def test_saved_reference_holdout_and_changed_source_invalidates_evidence(self):
        sample = self.store.add_sample({"text": "A fictional reference from somebody else.", "source_url": "https://example.test/post", "context": CONTEXT})
        packet = evidence_packet(source_kind="reference", source_id=sample["id"])
        row = self.learner.add_evidence(self.trait["id"], packet)
        self.store.update_sample(sample["id"], {"held_out": True})
        trait = self.learner.get_trait(self.trait["id"])
        self.assertEqual(trait["estimate"]["reviewed_evidence"], 0)
        self.assertFalse(trait["evidence"][0]["eligible"])
        with self.assertRaises(ValueError):
            self.learner.add_evidence(self.trait["id"], packet)

    def test_pasted_known_holdout_cannot_bypass_id_checks(self):
        text = 'This entirely fictional held-out passage contains enough separate words to test a substantial excerpt without using any real writing.'
        sample = self.store.add_sample({'text':text,'context':CONTEXT})
        self.store.update_sample(sample['id'], {'status':'confirmed','held_out':True})
        for passage in [text, ' '.join(text.split()[2:15])]:
            with self.assertRaisesRegex(ValueError, 'Held-out writing'):
                self.learner.add_evidence(self.trait['id'], evidence_packet(source_kind='reference', reference_text=passage))

    def test_evidence_pagination_never_truncates_the_estimate(self):
        for index in range(55):
            self.learner.add_evidence(self.trait['id'], evidence_packet(attribution='Independent synthetic preference observation %d' % index))
        trait = self.learner.get_trait(self.trait['id'])
        self.assertEqual(trait['estimate']['independent_groups'],55)
        self.assertEqual(len(trait['evidence']),50)
        self.assertTrue(trait['evidence_truncated'])
        second = self.learner.list_evidence(self.trait['id'], offset=50)
        self.assertEqual(len(second['items']),5)
        self.assertIsNone(second['next_offset'])
        self.assertEqual(len(self.learner.guidance(CONTEXT)[0]['evidence_ids']),55)

    def test_undone_correction_withdraws_its_influence_without_deleting_evidence(self):
        correction = CorrectionStore(self.path).add({"original_text": "We utilize a diagram.", "revised_text": "We use a diagram.",
            "guidance": "Use ordinary verbs", "context": CONTEXT, "scope": "channel", "kind": "style"})["correction"]
        event = self.learner.add_evidence(self.trait["id"], evidence_packet(source_kind="correction", source_id=correction["id"]))
        guidance = self.learner.guidance(CONTEXT)
        self.assertIn(correction["lesson_id"], guidance[0]["linked_lesson_ids"])
        self.assertEqual(self.learner.resolve(CONTEXT, exclude_source_ids=[correction["id"]])["preferences"], [])
        CorrectionStore(self.path).undo(correction["id"])
        result = self.learner.get_trait(self.trait["id"])
        self.assertEqual(result["total_evidence"], 1)
        self.assertEqual(result["estimate"]["inactive_sources"], 1)
        self.assertEqual(self.learner.guidance(CONTEXT), [])

    def test_same_edit_reimported_as_a_correction_is_one_group(self):
        before, after = "We utilize a diagram.", "We use a diagram."
        self.learner.add_evidence(self.trait["id"], evidence_packet(source_kind="edit", before_text=before, after_text=after, style_only=True, meaning_preserved=True))
        correction = CorrectionStore(self.path).add({"original_text": before, "revised_text": after,
            "guidance": "Use ordinary verbs", "context": CONTEXT, "scope": "channel", "kind": "style"})["correction"]
        self.learner.add_evidence(self.trait["id"], evidence_packet(source_kind="correction", source_id=correction["id"]))
        estimate = self.learner.get_trait(self.trait["id"])["estimate"]
        self.assertEqual(estimate["reviewed_evidence"], 2)
        self.assertEqual(estimate["independent_groups"], 1)
        self.assertEqual(estimate["effective_mass"], 1)

    def test_failed_audit_insert_rolls_back_the_evidence(self):
        with sqlite3.connect(str(self.path)) as conn:
            conn.execute("CREATE TRIGGER fail_pref_history BEFORE INSERT ON preference_evidence_history BEGIN SELECT RAISE(ABORT, 'synthetic history failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.learner.add_evidence(self.trait["id"], evidence_packet())
        self.assertEqual(self.learner.get_trait(self.trait["id"])["total_evidence"], 0)

    def test_failed_review_audit_preserves_pending_state(self):
        row = self.learner.add_evidence(self.trait["id"], evidence_packet(confirmed=False))
        with sqlite3.connect(str(self.path)) as conn:
            conn.execute("CREATE TRIGGER fail_pref_history BEFORE INSERT ON preference_evidence_history BEGIN SELECT RAISE(ABORT, 'synthetic history failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.learner.review_evidence(row["id"], "confirm", "I specifically prefer the concrete reasoning")
        self.assertEqual(self.learner.get_trait(self.trait["id"])["evidence"][0]["status"], "pending")


class ComparisonEvidenceTests(_LearnerFixture, unittest.TestCase):
    def practice_feedback(self, provider='codex', choice='a'):
        round = self.store.add_round(CONTEXT, {'provider': provider, 'options': [
            {'id': 'a', 'text': 'We utilize a diagram.'}, {'id': 'b', 'text': 'We use a diagram.'}], 'warnings': []}, [])
        return self.store.add_feedback({'round_id': round['id'], 'choice': choice,
            'edited_text': 'We use a diagram.', 'notes': 'Synthetic ordinary-word preference.'})

    def test_practice_vote_requires_explicit_aspect_style_and_meaning_review(self):
        feedback = self.practice_feedback()
        packet = evidence_packet(source_kind='practice', source_id=feedback['id'])
        with self.assertRaises(ValueError):
            self.learner.add_evidence(self.trait['id'], packet)
        before = self.store.feedback()
        event = self.learner.add_evidence(self.trait['id'], {**packet, 'style_only': True, 'meaning_preserved': True})
        self.assertEqual(self.learner.get_trait(self.trait['id'])['estimate']['effective_mass'], .5)
        self.assertEqual(before, self.store.feedback())
        with sqlite3.connect(str(self.path)) as conn:
            conn.execute("UPDATE feedback SET notes='A changed judgment' WHERE id=?", (feedback['id'],))
        self.assertFalse(self.learner.get_trait(self.trait['id'])['evidence'][0]['eligible'])

    def test_offline_and_neither_practice_are_not_directional_evidence(self):
        for feedback in (self.practice_feedback(provider='offline'), self.practice_feedback(choice='neither')):
            with self.assertRaises(ValueError):
                self.learner.add_evidence(self.trait['id'], evidence_packet(source_kind='practice', source_id=feedback['id'], style_only=True, meaning_preserved=True))

    def test_practice_edit_and_the_same_manual_edit_share_a_group(self):
        feedback = self.practice_feedback()
        self.learner.add_evidence(self.trait['id'], evidence_packet(source_kind='practice', source_id=feedback['id'], style_only=True, meaning_preserved=True))
        self.learner.add_evidence(self.trait['id'], evidence_packet(source_kind='edit', before_text='We utilize a diagram.', after_text=feedback['edited_text'], style_only=True, meaning_preserved=True))
        estimate = self.learner.get_trait(self.trait['id'])['estimate']
        self.assertEqual(estimate['independent_groups'], 1)
        self.assertEqual(estimate['effective_mass'], 1)

    def rated_evaluation(self, *, provider="codex", choice="a", factual=False, rated=True):
        EvaluationStore(self.path)
        ident = "fixture-" + str(random.random())
        with sqlite3.connect(str(self.path)) as conn:
            conn.execute("INSERT INTO eval_cases (id,context,brief,options,provider,model,controls,mapping,provider_warnings,status,created_at,choice,factual_issue,notes,rated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                ident, json.dumps(CONTEXT), "A fictional brief", json.dumps([{"id": "a", "text": "One fictional style"}, {"id": "b", "text": "Another fictional style"}]),
                provider, "synthetic", "{}", '{"a":"baseline","b":"calibrated"}', '{"baseline":[],"calibrated":[]}',
                "rated" if rated else "unrated", "2026-01-01", choice if rated else None, int(factual), "Synthetic review", "2026-01-02" if rated else None))
        return ident

    def test_unrated_and_offline_comparisons_never_train(self):
        for ident in [self.rated_evaluation(rated=False), self.rated_evaluation(provider="offline")]:
            with self.assertRaises(ValueError):
                self.learner.add_evidence(self.trait["id"], evidence_packet(source_kind="evaluation", source_id=ident))

    def test_factual_flags_ties_and_neither_do_not_become_style_feedback(self):
        for ident in [self.rated_evaluation(factual=True), self.rated_evaluation(choice="tie"), self.rated_evaluation(choice="neither")]:
            with self.assertRaises(ValueError):
                self.learner.add_evidence(self.trait["id"], evidence_packet(source_kind="evaluation", source_id=ident))

    def test_rated_comparison_needs_explicit_attribution_and_is_downweighted(self):
        ident = self.rated_evaluation()
        with self.assertRaises(ValueError):
            self.learner.add_evidence(self.trait["id"], evidence_packet(source_kind="evaluation", source_id=ident, attribution=""))
        event = self.learner.add_evidence(self.trait["id"], evidence_packet(source_kind="evaluation", source_id=ident, confirmed=False))
        self.assertEqual(self.learner.get_trait(self.trait["id"])["estimate"]["confidence"], 0)
        self.learner.review_evidence(event["id"], "confirm", "It was the concrete explanation I preferred")
        self.assertEqual(self.learner.get_trait(self.trait["id"])["estimate"]["effective_mass"], .25)
        with sqlite3.connect(str(self.path)) as conn:
            self.assertEqual(conn.execute("SELECT choice FROM eval_cases WHERE id=?", (ident,)).fetchone()[0], "a")

    def test_changed_or_missing_comparison_withdraws_its_evidence(self):
        ident = self.rated_evaluation()
        self.learner.add_evidence(self.trait["id"], evidence_packet(source_kind="evaluation", source_id=ident))
        with sqlite3.connect(str(self.path)) as conn:
            conn.execute("DELETE FROM eval_cases WHERE id=?", (ident,))
        result = self.learner.get_trait(self.trait["id"])
        self.assertEqual(result["estimate"]["inactive_sources"], 1)
        self.assertFalse(result["evidence"][0]["eligible"])

    def quality_case(self, meaning='preserved'):
        QualityStore(self.path)
        ident = 'quality-fixture-' + str(random.random())
        with sqlite3.connect(str(self.path)) as conn:
            conn.execute("INSERT INTO quality_cases (id,correction_id,context,brief,options,provider,model,controls,provenance,mapping,generation_order,provider_warnings,status,created_at,choice,meaning,effort,notes,rated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                ident, 'synthetic-correction', json.dumps(CONTEXT), 'A fresh fictional brief', '[{"id":"a","text":"A"},{"id":"b","text":"B"}]',
                'codex', 'synthetic', '{}', '{"lesson_id":"linked-fixture"}', '{"a":"updated","b":"previous"}', '[]', '{}', 'rated',
                '2026-01-01', 'a', json.dumps({'a':meaning,'b':'preserved'}), '{"a":"none","b":"none"}', 'Synthetic review', '2026-01-02'))
        return ident

    def test_quality_comparison_requires_both_meanings_preserved(self):
        for meaning in ['changed','unsure']:
            with self.assertRaisesRegex(ValueError, 'preserve meaning'):
                self.learner.add_evidence(self.trait['id'], evidence_packet(source_kind='quality', source_id=self.quality_case(meaning)))
        event = self.learner.add_evidence(self.trait['id'], evidence_packet(source_kind='quality', source_id=self.quality_case()))
        self.assertEqual(event['linked_lesson_id'], 'linked-fixture')
        self.assertEqual(self.learner.get_trait(self.trait['id'])['estimate']['effective_mass'], .9)


if __name__ == "__main__":
    unittest.main()
