import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

import test_server
from voicework import story_bridge
from voicework.story_bridge import available_sources, build_story_voice


def lesson(ident, channel, created, **fields):
    context = {"channel": channel, "audience": "", "intent": "", "stakes": "", "relationship": "", "scenario": ""}
    context.update(fields.pop("context", {}))
    base = {"id": ident, "context": context, "dimension": "diction", "guidance": "", "preferred": "",
            "avoided": "", "enabled": True, "created_at": created}
    base.update(fields)
    return base


def sample(ident, channel, text, status="confirmed", held_out=False, created="2026-01-01"):
    return {"id": ident, "title": ident, "text": text, "status": status, "held_out": held_out,
            "context": {"channel": channel}, "created_at": created}


class StoryBridgeUnitTests(unittest.TestCase):
    def test_lessons_keep_scope_and_become_valid_story_engine_profile(self):
        lessons = [
            lesson("a", "long-form writing", "2026-01-02", guidance="Lead with the concrete problem before the lesson."),
            lesson("b", "work email", "2026-01-03", preferred="use", avoided="utilize",
                   context={"audience": "teammates", "intent": "request review"}),
            lesson("off", "long-form writing", "2026-01-04", guidance="Disabled guidance", enabled=False),
        ]
        voice = build_story_voice([], [], lessons, "spoken interview answer", ["long-form writing", "work email"])
        self.assertEqual((voice["format"], voice["version"]), ("voicework.story-voice", 1))
        profile = voice["voice_profile"]
        self.assertEqual(set(profile), {"context", "traits", "avoid"})
        self.assertTrue(profile["context"].startswith("spoken interview answer — learned from Voicework evidence in:"))
        joined = "\n".join(profile["traits"])
        self.assertIn("[long-form writing] Diction: Lead with the concrete problem", joined)
        self.assertIn("[work email · audience: teammates · intent: request review] Diction: prefer wording like “use” over “utilize”.", joined)
        self.assertNotIn("Disabled guidance", joined)
        self.assertEqual(profile["avoid"], ["[work email · audience: teammates · intent: request review] Diction: wording like “utilize” (prefer “use”)"])
        # Broad lessons outrank newer, narrowly conditioned ones.
        self.assertTrue(profile["traits"][0].startswith("[long-form writing]"))
        self.assertEqual(voice["provenance"]["lesson_ids"], ["a", "b"])
        self.assertEqual(voice["voice_samples"], [])

    def test_unselected_channels_never_leak(self):
        lessons = [lesson("t", "text message", "2026-01-01", guidance="lowercase, no periods"),
                   lesson("l", "long-form writing", "2026-01-01", guidance="Short paragraphs.")]
        voice = build_story_voice([], [], lessons, "short story", ["long-form writing"])
        self.assertNotIn("lowercase", json.dumps(voice))

    def test_aliased_channel_selection_is_explicit_alias_only(self):
        lessons = [lesson("l", "longform writing", "2026-01-01", guidance="Short paragraphs.")]
        voice = build_story_voice([], [], lessons, "short story", ["Long-form writing"])
        self.assertEqual(voice["provenance"]["counts"]["lessons_used"], 1)

    def test_examples_require_opt_in_and_only_confirmed_non_held_out(self):
        samples = [sample("ok", "essay", "I wrote this. It's mine.", created="2026-01-03"),
                   sample("held", "essay", "Held out text.", held_out=True),
                   sample("pending", "essay", "Pending text.", status="pending"),
                   sample("ok2", "essay", "Another one I wrote. Short.", created="2026-01-02")]
        without = build_story_voice(samples, [], [], "short story", ["essay"])
        self.assertEqual(without["voice_samples"], [])
        self.assertEqual(without["provenance"]["counts"]["samples_measured"], 2)
        self.assertTrue(any("Sentences average" in trait for trait in without["voice_profile"]["traits"]))
        self.assertTrue(any("provisional" in trait for trait in without["voice_profile"]["traits"]))
        both = build_story_voice(samples, [], [lesson("w", "work email", "2026-01-01", guidance="Brief.")], "story", ["essay", "work email"])
        pattern = next(trait for trait in both["voice_profile"]["traits"] if "Sentences average" in trait)
        self.assertIn("· essay]", pattern)
        with_examples = build_story_voice(samples, [], [], "short story", ["essay"], include_examples=True)
        self.assertEqual(with_examples["voice_samples"], ["I wrote this. It's mine.", "Another one I wrote. Short."])
        dumped = json.dumps(with_examples)
        self.assertNotIn("Held out text", dumped)
        self.assertNotIn("Pending text", dumped)

    def test_feedback_notes_and_story_relevant_presentation_only(self):
        feedback = [{"id": "f", "context": {"channel": "essay"}, "notes": "Too stiff; I'd say it plainly.",
                     "presentation": {"paragraph_length": "short", "emoji": "lots", "signoff": "Cheers"},
                     "edited_text": "My edited AI option", "chosen_text": "AI option", "created_at": "2026-01-01"}]
        voice = build_story_voice([], feedback, [], "spoken interview answer", ["essay"])
        dumped = json.dumps(voice, ensure_ascii=False)
        self.assertIn("Stated reason when choosing between practice drafts: “Too stiff; I'd say it plainly.”", dumped)
        self.assertIn("Presentation — paragraph length: short", dumped)
        for absent in ("lots", "Cheers", "My edited AI option", "AI option"):
            self.assertNotIn(absent, dumped)

    def test_no_guidance_gives_null_profile_with_warning(self):
        voice = build_story_voice([sample("one", "essay", "Only one.")], [], [], "short story", ["essay"])
        self.assertIsNone(voice["voice_profile"])
        self.assertTrue(voice["provenance"]["warnings"])

    def test_caps_prefer_explicit_teaching_and_report_truncation(self):
        lessons = [lesson("l%02d" % index, "essay", "2026-01-%02d" % (index % 28 + 1), guidance="Habit number %d." % index)
                   for index in range(40)]
        samples = [sample("s%d" % index, "essay", "One sentence here. Another one.") for index in range(3)]
        voice = build_story_voice(samples, [], lessons, "short story", ["essay"])
        traits = voice["voice_profile"]["traits"]
        self.assertEqual(len(traits), story_bridge.MAX_TRAITS)
        measured = [trait for trait in traits if trait.startswith("[observed in")]
        # Measured habits keep their reserved places; lessons fill the rest, broadest and newest first.
        self.assertTrue(1 <= len(measured) <= story_bridge.MEASURED_SLOTS)
        self.assertTrue(any("Sentences average" in trait for trait in measured))
        self.assertEqual(len(traits) - len(measured), story_bridge.MAX_TRAITS - len(measured))
        self.assertTrue(all(not trait.startswith("[observed in") for trait in traits[:story_bridge.MAX_TRAITS - len(measured)]))
        self.assertTrue(any("highest-priority" in warning for warning in voice["provenance"]["warnings"]))

    def test_reserved_measurements_are_shared_across_channels(self):
        lessons = [lesson("l%02d" % index, "chat", "2026-01-%02d" % (index % 28 + 1), guidance="Habit %d." % index) for index in range(40)]
        samples = ([sample("c%d" % i, "chat", text) for i, text in enumerate(CHAT)] +
                   [sample("e%d" % i, "essay", text) for i, text in enumerate(ESSAY)])
        available = build_story_voice(samples, [], [], "story", ["chat", "essay"])["voice_profile"]["traits"]
        traits = build_story_voice(samples, [], lessons, "story", ["chat", "essay"])["voice_profile"]["traits"]
        measured = [trait for trait in traits if trait.startswith("[observed in")]
        self.assertEqual(len(measured), min(len(available), story_bridge.MEASURED_SLOTS))
        self.assertEqual(len(traits), story_bridge.MAX_TRAITS)
        self.assertTrue(any("· chat]" in trait for trait in measured) and any("· essay]" in trait for trait in measured))
        self.assertTrue(measured[0].startswith("[observed in 4 confirmed samples · chat] Sentences average"))
        self.assertTrue(measured[1].startswith("[observed in 3 confirmed samples · essay] Sentences average"))

    def test_long_examples_are_excerpted_and_text_is_sanitized(self):
        long_text = ("A sentence that keeps going. " * 120) + "\x07"
        voice = build_story_voice([sample("long", "essay", long_text), sample("s2", "essay", "Fine.")], [],
                                  [lesson("l", "essay", "2026-01-01", guidance="Bell\x07 here")], "story", ["essay"], True)
        self.assertLessEqual(len(voice["voice_samples"][0]), story_bridge.MAX_EXAMPLE_CHARS + 2)
        self.assertNotIn("\x07", json.dumps(voice))

    def test_validation_errors(self):
        for target, channels in (("", ["essay"]), ("story", []), ("story", ["unknown"]), ("x" * 201, ["essay"])):
            with self.assertRaises(ValueError):
                build_story_voice([], [], [], target, channels)

    def test_sources_group_by_channel_and_suggest_only_broad_lessons(self):
        sources = available_sources(
            [sample("s", "Essay", "Text."), sample("p", "essay", "Text.", status="pending")],
            [{"context": {"channel": "work email"}}],
            [lesson("l", "long-form writing", "2026-01-01", guidance="x"), lesson("off", "essay", "2026-01-01", guidance="x", enabled=False)])
        by_key = {item["channel"]: item for item in sources}
        self.assertTrue(by_key["long-form writing"]["suggested"])
        self.assertEqual(by_key["essay"]["samples"], 1)
        self.assertEqual(by_key["essay"]["lessons"], 0)
        self.assertFalse(by_key["work email"]["suggested"])
        self.assertEqual(sources[0]["channel"], "long-form writing")


CHAT = [  # Synthetic, fictional texts with a deliberate chat register.
    "ngl the bus was late again\nhonestly fine\nsee you at 6",
    "ok so the tickets came through\nngl kinda thrilled\nbring snacks",
    "honestly cannot wait\nwe leave at 5\nngl i packed already",
    "running late, sorry\nsave me a seat\nhonestly the traffic is wild",
]
ESSAY = [  # Synthetic, fictional long-form passages.
    "The first draft worked on my desk. It failed on the train; nobody could find the menu.\n\nSo I moved it.",
    "I had assumed the hard part was the parser. It wasn't. The hard part was deciding what to do with input that was almost right.",
    "We shipped on a Thursday. By Friday I knew nobody reads tooltips, so I rewrote the empty state instead.",
]


def prior(ident, channel, text, confirmed=True, label="Background", created="2026-01-01"):
    return {"id": ident, "label": label, "text": text, "context": {"channel": channel}, "source": "test",
            "confirmed": confirmed, "created_at": created}


class StoryBridgeMeasurementTests(unittest.TestCase):
    def voice(self, **kwargs):
        samples = ([sample("c%d" % i, "personal chat", text, created="2026-02-0%d" % (i + 1)) for i, text in enumerate(CHAT)] +
                   [sample("e%d" % i, "long-form writing", text, created="2026-03-0%d" % (i + 1)) for i, text in enumerate(ESSAY)])
        return build_story_voice(samples, [], [], "spoken story", kwargs.pop("channels", ["personal chat", "long-form writing"]), **kwargs)

    def test_signature_wording_reports_rates_and_only_closed_class_markers(self):
        traits = self.voice()["voice_profile"]["traits"]
        signature = next(trait for trait in traits if "Signature wording" in trait)
        self.assertTrue(signature.startswith("[observed in 4 confirmed samples · personal chat]"))
        self.assertIn("“ngl” (3 of 4 samples)", signature)
        self.assertIn("“honestly” (3 of 4 samples)", signature)
        self.assertIn("at or below that rate", signature)
        # Subject words recur too, but never become signatures.
        self.assertNotIn("bus", signature)
        self.assertFalse(any("Signature wording" in trait and "long-form writing" in trait for trait in traits))

    def test_habits_are_measured_per_channel_with_their_own_scope(self):
        voice = self.voice()
        traits = voice["voice_profile"]["traits"]
        lowercase = next(trait for trait in traits if "lowercase" in trait)
        self.assertTrue(lowercase.startswith("[observed in 4 confirmed samples · personal chat]"))
        self.assertIn("keep it to that register", lowercase)
        lines = next(trait for trait in traits if "short lines" in trait)
        self.assertIn("personal chat", lines)
        unmarked = [trait for trait in traits if "without a closing period" in trait]
        self.assertEqual(len(unmarked), 1)  # Chat only; the long-form samples end their sentences.
        self.assertTrue(unmarked[0].startswith("[observed in 4 confirmed samples · personal chat]"))
        cadence = [trait for trait in traits if "Sentences average" in trait]
        self.assertEqual(len(cadence), 2)  # One per channel, never pooled.
        self.assertTrue(any("· long-form writing]" in trait for trait in cadence))
        self.assertTrue(any("run 6 words or fewer" in trait for trait in cadence))
        # Only one long-form sample has two paragraphs: too little to state a paragraph habit.
        self.assertFalse(any("Paragraphs average" in trait for trait in traits))
        # Four chat samples with no em dashes, exclamation marks or semicolons.
        self.assertIn("[observed in 4 confirmed samples · personal chat] No em dashes, exclamation marks, or semicolons "
                      "in any of 4 samples; do not add them.", voice["voice_profile"]["avoid"])
        self.assertEqual(voice["provenance"]["counts"]["samples_measured"], 7)

    def test_contractions_without_apostrophes_count_and_short_messages_never_claim_rarely(self):
        texts = ["cant make it sat\nknee is weird", "dont wait for me\nim close", "running late\nsave a seat", "ok see you soon"]
        traits = build_story_voice([sample("b%d" % i, "chat", text) for i, text in enumerate(texts)], [], [], "story", ["chat"])["voice_profile"]["traits"]
        bare = next(trait for trait in traits if "without apostrophes" in trait)
        self.assertIn("(“cant”, “dont”, “im”) in 2 of 4 samples", bare)
        self.assertFalse(any("Rarely uses contractions" in trait for trait in traits))  # Too little text to judge.
        formal = ["The committee met on a Tuesday and approved the plan without further discussion of the budget. " * 4] * 3
        traits = build_story_voice([sample("f%d" % i, "report", text + str(i)) for i, text in enumerate(formal)], [], [],
                                   "story", ["report"])["voice_profile"]["traits"]
        self.assertTrue(any("Rarely uses contractions (0 of 3 samples)" in trait for trait in traits))

    def test_possessives_dialogue_tags_and_abbreviations_are_not_misread(self):
        formal = ["The committee's report was final. The chair's vote decided the matter after a long debate about the budget. " * 2 + str(i)
                  for i in range(3)]
        traits = build_story_voice([sample("f%d" % i, "report", text) for i, text in enumerate(formal)], [], [], "story", ["report"])["voice_profile"]["traits"]
        self.assertFalse(any("Uses contractions naturally" in trait for trait in traits))
        dialogue = ['"Ready?" she asked. We left at 9 a.m. and waited for the ferry until it came in late.',
                    '"Now?" he said. The pier was empty... and the gulls had the whole place to themselves today.']
        cadence = next(trait for trait in build_story_voice([sample("d%d" % i, "story", text) for i, text in enumerate(dialogue)],
                                                            [], [], "story", ["story"])["voice_profile"]["traits"] if "Sentences average" in trait)
        self.assertIn("Sentences average about 9 words", cadence)  # Not 2-word fragments split at "Ready?" or "a.m."

    def test_topic_words_are_stemmed_consistently_and_generic_words_ignored(self):
        samples = [sample("a", "essay", "I sent the messages before lunch.", created="2026-01-04"),
                   sample("b", "essay", "Two weddings in one month wore us out.", created="2026-01-03"),
                   sample("c", "essay", "My sisters drove up for the weekend.", created="2026-01-02")]
        voice = build_story_voice(samples, [], [], "story", ["essay"], True, topic="my sister's wedding")
        self.assertEqual(voice["voice_samples"], ["I sent the messages before lunch."])
        # "messages" is a generic writing word, not a subject: the UI's own placeholder topic keeps that excerpt.
        voice = build_story_voice(samples, [], [], "story", ["essay"], True, topic="an undo button for messages")
        self.assertEqual(len(voice["voice_samples"]), 3)

    def test_regular_punctuation_is_reported_with_a_rate(self):
        texts = ["It worked — mostly. Then it didn't.", "Fine — I said so. Later, maybe.", "Close — but no. We tried again."]
        voice = build_story_voice([sample("p%d" % i, "essay", text) for i, text in enumerate(texts)], [], [], "story", ["essay"])
        punctuation = next(trait for trait in voice["voice_profile"]["traits"] if "Punctuation used regularly" in trait)
        self.assertIn("em dashes (in 3 of 3 samples, about", punctuation)
        self.assertEqual(voice["voice_profile"]["avoid"], [])  # Three samples are too few to state an absence.

    def test_facts_are_opt_in_confirmed_channel_scoped_and_make_version_two(self):
        lessons = [lesson("l", "long-form writing", "2026-01-01", guidance="Short paragraphs.")]
        priors = [prior("p1", "long-form writing", "I led a six-person support tools team in 2025.", created="2026-01-02"),
                  prior("p2", "long-form writing", "Unconfirmed claim.", confirmed=False),
                  prior("p3", "personal chat", "Lives near the river.")]
        without = build_story_voice([], [], lessons, "story", ["long-form writing"], priors=priors)
        self.assertEqual(without["version"], 1)
        self.assertNotIn("facts", without)
        self.assertNotIn("six-person", json.dumps(without))
        voice = build_story_voice([], [], lessons, "story", ["long-form writing"], priors=priors, include_facts=True)
        self.assertEqual(voice["version"], 2)
        self.assertEqual(voice["facts"], ["Background: I led a six-person support tools team in 2025."])
        self.assertEqual(list(voice)[:5], ["format", "version", "voice_profile", "voice_samples", "facts"])
        self.assertEqual(voice["provenance"]["counts"]["facts_included"], 1)
        dumped = json.dumps(voice)
        for absent in ("Unconfirmed claim", "river"):
            self.assertNotIn(absent, dumped)
        self.assertNotIn("six-person", json.dumps(voice["voice_profile"]))
        empty = build_story_voice([], [], lessons, "story", ["long-form writing"], include_facts=True)
        self.assertNotIn("facts", empty)
        self.assertTrue(any("no facts were included" in warning for warning in empty["provenance"]["warnings"]))
        many = [prior("m%02d" % i, "long-form writing", "Fact %d." % i, created="2026-01-%02d" % (i + 1)) for i in range(15)]
        capped = build_story_voice([], [], lessons, "story", ["long-form writing"], priors=many, include_facts=True)
        self.assertEqual(len(capped["facts"]), story_bridge.MAX_FACTS)
        self.assertEqual(capped["facts"][0], "Background: Fact 14.")

    def test_topic_skips_same_subject_excerpts_and_is_never_saved(self):
        samples = [sample("a", "essay", "Nobody could find the button on the bus.", created="2026-01-03"),
                   sample("b", "essay", "The parser was never the hard part.", created="2026-01-02"),
                   sample("c", "essay", "We shipped on a Thursday and slept on Friday.", created="2026-01-01")]
        voice = build_story_voice(samples, [], [], "story", ["essay"], True, topic="An undo button for sent messages")
        self.assertEqual(voice["voice_samples"], ["The parser was never the hard part.", "We shipped on a Thursday and slept on Friday."])
        self.assertEqual(voice["provenance"]["counts"]["examples_skipped_for_topic"], 1)
        self.assertNotIn("undo", json.dumps(voice))
        self.assertTrue(any("Skipped 1 excerpt" in warning for warning in voice["provenance"]["warnings"]))
        everything = build_story_voice(samples[:1], [], [], "story", ["essay"], True, topic="buttons")
        self.assertEqual(everything["voice_samples"], [])
        self.assertTrue(any("avoid the story's subject" in warning for warning in everything["provenance"]["warnings"]))
        with self.assertRaises(ValueError):
            build_story_voice(samples, [], [], "story", ["essay"], True, topic="x" * 301)

    def test_story_channels_are_suggested_and_aliased(self):
        lessons = [lesson("s", "Spoken stories", "2026-01-01", guidance="Say the decision plainly."),
                   lesson("w", "work email", "2026-01-01", guidance="Brief.")]
        sources = {item["channel"]: item for item in available_sources([], [], lessons)}
        self.assertTrue(sources["spoken story"]["suggested"])
        self.assertFalse(sources["work email"]["suggested"])
        voice = build_story_voice([], [], lessons, "spoken interview answer", ["spoken story"])
        self.assertEqual(voice["provenance"]["counts"]["lessons_used"], 1)

    def test_holdout_pack_contains_only_held_out_confirmed_text_from_chosen_channels(self):
        samples = [sample("h1", "essay", "Held out line one.\nSecond line kept.", held_out=True),
                   sample("h2", "work email", "Other channel held out.", held_out=True),
                   sample("h3", "essay", "Held but pending.", status="pending", held_out=True),
                   sample("ok", "essay", "Training text.")]
        samples[0]["context"]["audience"] = "friend"
        pack = story_bridge.build_holdout_pack(samples, ["essay"])
        self.assertEqual((pack["format"], pack["version"]), ("voicework.holdout", 1))
        self.assertEqual(pack["items"], [{"id": "h1", "context": "essay · audience: friend", "text": "Held out line one.\nSecond line kept."}])
        voice = build_story_voice(samples, [], [], "story", ["essay"], True)
        self.assertNotIn("Held out", json.dumps(voice))

    def test_holdout_pack_fits_story_engine_limits(self):
        long_context = {"channel": "essay", "audience": "a" * 120, "intent": "b" * 120, "relationship": "c" * 120,
                        "stakes": "d" * 120, "scenario": "e" * 160}
        samples = [dict(sample("h%02d" % i, "essay", "Held piece %d." % i, held_out=True, created="2026-01-%02d" % (i % 28 + 1)),
                        context=long_context) for i in range(55)]
        pack = story_bridge.build_holdout_pack(samples, ["essay"])
        self.assertEqual(len(pack["items"]), story_bridge.MAX_HOLDOUT_ITEMS)
        self.assertEqual(pack["total_held_out"], 55)
        self.assertTrue(all(len(item["context"]) <= 300 for item in pack["items"]))
        self.assertEqual(pack["items"][0]["text"], "Held piece 27.")  # Newest first (created 2026-01-28).


class StoryBridgeApiTests(unittest.TestCase):
    setUp = test_server.ServerTests.setUp
    tearDown = test_server.ServerTests.tearDown
    request = test_server.ServerTests.request

    def test_story_engine_export_route_and_state_sources(self):
        lesson_body = {"context": {"channel": "long-form writing"}, "dimension": "rhythm",
                       "guidance": "Let a short sentence land after a longer one."}
        self.assertEqual(self.request("POST", "/api/language-lessons", lesson_body)[0], 201)
        state = self.request("GET", "/api/state")[1]
        self.assertEqual(state["story_engine_sources"][0]["channel"], "long-form writing")
        status, value, _ = self.request("GET", "/api/export/story-engine?target=spoken+interview+answer&channel=long-form+writing")
        self.assertEqual(status, 200)
        self.assertEqual(value["filename"], "story-voice.json")
        self.assertIn("Let a short sentence land", value["story_voice"]["voice_profile"]["traits"][0])
        self.assertEqual(self.request("GET", "/api/export/story-engine?channel=long-form+writing")[0], 400)
        self.assertEqual(self.request("GET", "/api/export/story-engine?target=x")[0], 400)
        self.assertEqual(self.request("GET", "/api/export/story-engine?target=x&channel=essay&bogus=1")[0], 400)

    def test_cli_reads_data_dir_and_creates_private_new_file(self):
        self.request("POST", "/api/language-lessons", {"context": {"channel": "essay"}, "dimension": "diction",
                                                       "preferred": "use", "avoided": "utilize"})
        data_dir = Path(self.temp.name) / "data"
        output = Path(self.temp.name) / "story-voice.json"
        with redirect_stdout(StringIO()) as listing:
            self.assertEqual(story_bridge.main(["--data-dir", str(data_dir), "--list"]), 0)
        self.assertIn("essay", listing.getvalue())
        with redirect_stderr(StringIO()):
            self.assertEqual(story_bridge.main(["--data-dir", str(data_dir), "--target", "short story",
                                                "--channel", "essay", "--output", str(output)]), 0)
            self.assertEqual(story_bridge.main(["--data-dir", str(data_dir), "--target", "short story",
                                                "--channel", "essay", "--output", str(output)]), 2)
        self.assertEqual(os.stat(output).st_mode & 0o777, 0o600)
        self.assertIn("utilize", json.loads(output.read_text())["voice_profile"]["avoid"][0])
        with redirect_stderr(StringIO()):
            self.assertEqual(story_bridge.main(["--data-dir", str(Path(self.temp.name) / "missing"), "--list"]), 2)

    def test_export_route_and_cli_add_facts_topic_and_holdout_pack(self):
        self.request("POST", "/api/language-lessons", {"context": {"channel": "essay"}, "dimension": "rhythm",
                                                       "guidance": "Land the point in a short sentence."})
        prior = self.request("POST", "/api/priors", {"label": "Work", "text": "I maintain a field-service search tool.",
                                                     "context": {"channel": "essay"}, "source": "test"})[1]["prior"]
        self.request("PATCH", "/api/priors/" + prior["id"], {"confirmed": True})
        ids = []
        for text in ("I fixed the sync on a Tuesday. It held.", "Nobody read the manual, so I moved the button.",
                     "Held-out synthetic line for evaluation."):
            ident = self.request("POST", "/api/samples", {"title": text[:20], "text": text, "context": {"channel": "essay"}})[1]["sample"]["id"]
            self.request("PATCH", "/api/samples/" + ident, {"status": "confirmed"})
            ids.append(ident)
        self.request("PATCH", "/api/samples/" + ids[2], {"held_out": True})
        status, value, _ = self.request("GET", "/api/export/story-engine?target=story&channel=essay&examples=1&facts=1&topic=the+button")
        self.assertEqual(status, 200)
        voice = value["story_voice"]
        self.assertEqual(voice["version"], 2)
        self.assertEqual(voice["facts"], ["Work: I maintain a field-service search tool."])
        self.assertEqual(voice["voice_samples"], ["I fixed the sync on a Tuesday. It held."])
        self.assertNotIn("Held-out synthetic", json.dumps(voice))
        self.assertEqual(self.request("GET", "/api/export/story-engine?target=x&channel=essay&facts=1&facts=1")[0], 400)
        self.assertEqual(self.request("GET", "/api/export/story-engine?target=x&channel=essay&topic=" + "x" * 301)[0], 400)

        data_dir = Path(self.temp.name) / "data"
        output, holdout = Path(self.temp.name) / "voice.json", Path(self.temp.name) / "holdout.json"
        with redirect_stderr(StringIO()) as stderr:
            self.assertEqual(story_bridge.main(["--data-dir", str(data_dir), "--target", "story", "--channel", "essay",
                                                "--facts", "--output", str(output), "--holdout-output", str(holdout)]), 0)
        self.assertIn("1 held-out samples", stderr.getvalue())
        self.assertEqual(os.stat(holdout).st_mode & 0o777, 0o600)
        pack = json.loads(holdout.read_text())
        self.assertEqual([item["text"] for item in pack["items"]], ["Held-out synthetic line for evaluation."])
        self.assertNotIn("Held-out synthetic", output.read_text())
        self.assertEqual(json.loads(output.read_text())["version"], 2)
        # An existing destination refuses before anything is written.
        fresh = Path(self.temp.name) / "fresh.json"
        with redirect_stderr(StringIO()):
            self.assertEqual(story_bridge.main(["--data-dir", str(data_dir), "--target", "story", "--channel", "essay",
                                                "--output", str(fresh), "--holdout-output", str(holdout)]), 2)
            self.assertEqual(story_bridge.main(["--data-dir", str(data_dir), "--target", "story", "--channel", "work email",
                                                "--holdout-output", str(Path(self.temp.name) / "none.json")]), 2)
        self.assertFalse(fresh.exists())
        same = Path(self.temp.name) / "same.json"
        missing_dir = Path(self.temp.name) / "missing" / "holdout.json"
        with redirect_stderr(StringIO()) as stderr:
            self.assertEqual(story_bridge.main(["--data-dir", str(data_dir), "--target", "story", "--channel", "essay",
                                                "--output", str(same), "--holdout-output", str(same)]), 2)
            self.assertEqual(story_bridge.main(["--data-dir", str(data_dir), "--target", "story", "--channel", "essay",
                                                "--output", str(fresh), "--holdout-output", str(missing_dir)]), 2)
        self.assertIn("must be different files", stderr.getvalue())
        self.assertFalse(same.exists())
        self.assertFalse(fresh.exists())  # The voice file is removed when the holdout file cannot be written.


if __name__ == "__main__":
    unittest.main()
