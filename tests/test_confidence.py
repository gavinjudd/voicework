import copy
import json
import unittest

from voicework.confidence import build_voice_confidence


def sample(text="An authored example.", **fields):
    return {"text": text, "status": "confirmed", "held_out": False,
            "context": {"channel": "work"}, **fields}


def lesson(**fields):
    return {"enabled": True, "guidance": "Keep paragraphs short.",
            "context": {"channel": "work"}, **fields}


def case(brief="The review is Friday.", channel="work", **fields):
    return {"provider": "codex", "status": "rated", "result": "calibrated",
            "factual_issue": False, "brief": brief, "context": {"channel": channel}, **fields}


class ConfidenceTests(unittest.TestCase):
    def test_empty_state_has_no_implied_accuracy(self):
        summary = build_voice_confidence([], [])
        self.assertEqual(summary["label"], "Evidence needed")
        self.assertEqual((summary["completed"], summary["total"]), (0, 3))
        self.assertEqual([item["complete"] for item in summary["milestones"]], [False] * 3)
        self.assertEqual(summary["evidence"], {"authored_samples": 0, "feedback": 0, "lessons": 0, "channels": []})
        self.assertTrue(all(value == 0 for key, value in summary["validation"].items() if key != "channels"))
        self.assertIn("not measured writing accuracy", summary["note"])
        self.assertIn("do not validate the current profile", summary["note"])
        self.assertNotIn("%", json.dumps(summary))

    def test_pending_assisted_rejected_holdout_and_empty_samples_are_excluded(self):
        excluded = [sample(status=status) for status in ("pending", "assisted", "rejected")]
        excluded += [sample(held_out=True), sample(text=" \n\t "), {"text": "Unreviewed"}]
        summary = build_voice_confidence(excluded, [])
        self.assertEqual(summary["completed"], 0)
        self.assertEqual(summary["evidence"]["authored_samples"], 0)
        self.assertEqual(summary["evidence"]["channels"], [])

    def test_authored_text_is_deduplicated_by_case_and_whitespace(self):
        samples = [sample("The review is Friday."), sample(" THE  REVIEW\nis Friday. "),
                   sample("A different authored example."), sample("An excluded unique sample.", held_out=True)]
        summary = build_voice_confidence(samples, [])
        self.assertEqual(summary["evidence"]["authored_samples"], 2)
        self.assertEqual(summary["completed"], 1)
        self.assertEqual(summary["label"], "Evidence only")

    def test_enabled_nonempty_lessons_work_without_authored_samples(self):
        lessons = [lesson(), lesson(enabled=False, context={"channel": "personal"}), lesson(guidance="  ")]
        summary = build_voice_confidence([], [], lessons)
        self.assertEqual(summary["completed"], 1)
        self.assertEqual(summary["evidence"], {"authored_samples": 0, "feedback": 0, "lessons": 1, "channels": ["work"]})
        self.assertEqual(build_voice_confidence([], [], [lesson(guidance="", preferred="Let's go.")])["completed"], 1)

    def test_nonempty_practice_preferences_count_separately_from_authorship(self):
        preferences = [
            {"notes": "Use plain wording."},
            {"edited_text": "The revised wording."},
            {"presentation": {"structure": "prose"}},
            {"choice": "a", "chosen_text": "Preferred wording."},
            {"choice": "b", "options": [{"id": "b", "text": "Another preference."}]},
            {"choice": "neither", "options": [{"id": "a", "text": "Rejected wording."}]},
        ]
        blank = [{}, {"notes": "  ", "edited_text": "\n", "presentation": {"structure": " "}},
                 {"choice": "a"}, {"choice": "neither", "options": []}]
        summary = build_voice_confidence([], preferences + blank)
        self.assertEqual(summary["completed"], 1)
        self.assertEqual(summary["evidence"]["feedback"], len(preferences))
        self.assertEqual(summary["evidence"]["authored_samples"], 0)
        self.assertEqual(build_voice_confidence([], blank)["completed"], 0)

    def test_offline_unrated_and_other_providers_do_not_validate(self):
        excluded = [case(provider="offline"), case(status="unrated"), case(provider="other")]
        summary = build_voice_confidence([sample()], [], evaluations=excluded)
        self.assertEqual(summary["completed"], 1)
        self.assertEqual(summary["validation"]["rated_real"], 0)
        self.assertEqual(summary["validation"]["calibrated_wins"], 0)
        self.assertEqual(summary["validation"]["distinct_briefs"], 0)
        self.assertEqual(summary["validation"]["channels"], [])

    def test_correction_checks_never_fill_overall_comparison_milestones(self):
        checks = [case(channel="work"), case(channel="personal"), case("A third brief."),
                  case(provider="offline"), case(status="unrated")]
        summary = build_voice_confidence([], [], [lesson()], quality_checks=checks)
        self.assertEqual(summary["completed"], 1)
        self.assertEqual(summary["validation"]["correction_checks"], 3)
        self.assertEqual(summary["validation"]["rated_real"], 0)
        self.assertEqual(summary["validation"]["distinct_briefs"], 0)

    def test_outcomes_and_factual_flags_are_independent_observations(self):
        cases = [case(result="calibrated", factual_issue=True), case(result="baseline", factual_issue=True),
                 case(result="tie"), case(result="neither")]
        validation = build_voice_confidence([sample()], [], evaluations=cases)["validation"]
        self.assertEqual({key: validation[key] for key in ("rated_real", "calibrated_wins", "baseline_wins", "ties", "neither", "factual_flags")},
                         {"rated_real": 4, "calibrated_wins": 1, "baseline_wins": 1, "ties": 1, "neither": 1, "factual_flags": 2})

    def test_all_negative_outcomes_do_not_imply_voice_success(self):
        cases = [case("First situation.", result="baseline", factual_issue=True),
                 case("Second situation.", channel="personal", result="neither"),
                 case("Third situation.", result="baseline")]
        summary = build_voice_confidence([sample()], [], evaluations=cases)
        self.assertEqual(summary["completed"], 3)
        self.assertEqual(summary["label"], "Compared across contexts")
        self.assertEqual(summary["validation"]["calibrated_wins"], 0)
        self.assertEqual(summary["validation"]["baseline_wins"], 2)
        self.assertEqual(summary["validation"]["factual_flags"], 1)
        self.assertIn("2 baseline picks", summary["milestones"][1]["detail"])
        self.assertIn("Review the comparison outcomes", summary["next_step"])
        self.assertIn("not measured writing accuracy", summary["note"])

    def test_repeated_context_and_brief_only_count_once(self):
        cases = [case(context={"channel": "work", "audience": "Team"}),
                 case(" THE REVIEW  IS\nFRIDAY. ", context={"channel": " WORK ", "audience": " team "}),
                 case(channel="personal"), case(channel="personal")]
        summary = build_voice_confidence([sample()], [], evaluations=cases)
        self.assertEqual(summary["validation"]["rated_real"], 4)
        self.assertEqual(summary["validation"]["distinct_briefs"], 2)
        self.assertEqual(summary["validation"]["channels"], ["personal", "work"])
        self.assertEqual(summary["completed"], 2)

    def test_three_distinct_cases_also_require_two_channels(self):
        cases = [case("First situation."), case("Second situation."), case("Third situation.")]
        summary = build_voice_confidence([sample()], [], evaluations=cases)
        self.assertEqual(summary["validation"]["distinct_briefs"], 3)
        self.assertEqual(summary["completed"], 2)
        cases[-1]["context"]["channel"] = "personal"
        self.assertEqual(build_voice_confidence([sample()], [], evaluations=cases)["completed"], 3)

    def test_custom_channels_normalize_and_unknown_or_blank_channels_do_not_add_breadth(self):
        cases = [case(channel="Client  Email"), case(channel=" client email "),
                 case("Second situation.", channel="Team Chat"), case("Third situation.", channel="UNKNOWN"),
                 case("Fourth situation.", channel=""), case("Fifth situation.", context={})]
        summary = build_voice_confidence([sample(context={"channel": " CLIENT  Email "})], [],
                                         [lesson(context={"channel": "unknown"})], evaluations=cases)
        self.assertEqual(summary["validation"]["rated_real"], 6)
        self.assertEqual(summary["validation"]["channels"], ["client email", "team chat"])
        self.assertEqual(summary["validation"]["distinct_briefs"], 2)
        self.assertEqual(summary["evidence"]["channels"], ["client email"])
        self.assertEqual(summary["completed"], 2)

    def test_context_fields_and_channel_distinguish_the_same_brief(self):
        cases = [case(), case(channel="personal"), case(context={"channel": "work", "audience": "manager"})]
        summary = build_voice_confidence([], [], [lesson()], evaluations=cases)
        self.assertEqual(summary["validation"]["distinct_briefs"], 3)
        self.assertEqual(summary["completed"], 3)
        for field in ("audience", "intent", "relationship", "stakes", "scenario"):
            with self.subTest(field=field):
                cases = [case(), case(context={"channel": "work", field: "different context"})]
                self.assertEqual(build_voice_confidence([], [], [lesson()], evaluations=cases)["validation"]["distinct_briefs"], 2)

    def test_checkpoints_are_cumulative_when_current_evidence_is_removed(self):
        cases = [case(), case(channel="personal"), case("Another brief.")]
        summary = build_voice_confidence([], [], [lesson(enabled=False)], evaluations=cases)
        self.assertEqual(summary["completed"], 0)
        self.assertEqual(summary["validation"]["rated_real"], 3)
        self.assertEqual(summary["validation"]["distinct_briefs"], 3)
        self.assertEqual([item["complete"] for item in summary["milestones"]], [False] * 3)

    def test_summary_does_not_mutate_or_expose_private_record_content(self):
        inputs = ([sample("SECRET AUTHORED TEXT"), sample("SECRET HOLDOUT TEXT", held_out=True)],
                  [{"notes": "SECRET PRACTICE NOTES"}], [lesson(guidance="SECRET LESSON")],
                  [case("SECRET BRIEF", status="unrated", options=[{"id": "a", "text": "SECRET OPTION"}],
                        mapping={"a": "SECRET MAPPING"})],
                  [case("SECRET QUALITY BRIEF", status="unrated", provenance={"secret": "SECRET PROVENANCE"})])
        original = copy.deepcopy(inputs)
        summary = build_voice_confidence(*inputs)
        self.assertEqual(inputs, original)
        self.assertNotIn("SECRET", json.dumps(summary))
        self.assertEqual(summary["validation"]["rated_real"], 0)


if __name__ == "__main__":
    unittest.main()
