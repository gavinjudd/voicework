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
        self.assertEqual(len(voice["voice_profile"]["traits"]), story_bridge.MAX_TRAITS)
        self.assertFalse(any("Sentences average" in trait for trait in voice["voice_profile"]["traits"]))
        self.assertTrue(any("highest-priority" in warning for warning in voice["provenance"]["warnings"]))

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


if __name__ == "__main__":
    unittest.main()
