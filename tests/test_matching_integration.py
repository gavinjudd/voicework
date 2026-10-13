"""Context matching stays consistent at generation and correction boundaries."""
import copy
import json
import unittest
from unittest.mock import patch

import test_server
from voicework.corrections import lesson_fingerprint
from voicework.language import matching_lessons
from voicework.provider import Provider, _payload


def context(**changes):
    return {"channel": "work", "audience": "teammate", "relationship": "coworker", **changes}


def sample(ident, **changes):
    return {"id": ident, "text": "Synthetic authored reference " + ident,
            "status": "confirmed", "held_out": False, "context": context(), **changes}


class MatchingProviderIntegrationTests(unittest.TestCase):
    def test_direct_provider_entry_filters_evidence_and_feedback_before_payload_and_ids(self):
        requested = context()
        incompatible = context(audience="client", relationship="client")
        evidence = [sample("eligible"), sample("pending", status="pending"),
                    sample("assisted", status="assisted"), sample("rejected", status="rejected"),
                    sample("held-out", held_out=True), sample("other-audience", context=incompatible),
                    sample("other-channel", context=context(channel="personal"))]
        preferences = [{"id": "eligible", "context": requested, "notes": "Keep it concise."},
                       {"id": "other-audience", "context": incompatible, "notes": "Use a sales greeting."},
                       {"id": "other-channel", "context": context(channel="personal"), "notes": "Use an affectionate signoff."}]
        original = copy.deepcopy((evidence, preferences, requested))
        provider = Provider("codex")
        with patch.object(provider, "_invoke", return_value={"text": "A synthetic draft.", "warnings": []}) as invoke:
            result = provider.generate_draft(requested, "The meeting starts Friday.", {}, evidence, preferences)
        payload = invoke.call_args.args[1]
        self.assertEqual([item["id"] for item in payload["authored_examples"]], ["eligible"])
        self.assertEqual([item["id"] for item in payload["calibration_feedback"]], ["eligible"])
        self.assertEqual(result["evidence_ids"], ["eligible"])
        self.assertEqual((evidence, preferences, requested), original)

        with patch.object(provider, "_invoke", return_value={"options": [{"text": "First."}, {"text": "Second."}], "warnings": []}) as invoke:
            provider.generate_round(requested, evidence, preferences)
        self.assertEqual(invoke.call_args.args[1]["authored_examples"], payload["authored_examples"])
        self.assertEqual(invoke.call_args.args[1]["calibration_feedback"], payload["calibration_feedback"])

    def test_offline_structure_uses_only_compatible_feedback(self):
        incompatible = {"context": context(audience="client", relationship="client"),
                        "presentation": {"structure": "bullets"}}
        eligible = {"context": context(), "presentation": {"structure": "prose"}}
        result = Provider().generate_draft(context(), "First fact.\nSecond fact.",
                                           {"structure": "auto"}, [], [incompatible, eligible])
        self.assertEqual(result["text"], "First fact. Second fact.")

    def test_offline_nonfeedback_records_cannot_crowd_out_structure_preference(self):
        lessons = [{"id": "lesson-" + str(index), "context": context(), "is_language_lesson": True,
                    "enabled": True, "guidance": "Keep it clear.", "created_at": "2026-02-01"}
                   for index in range(13)]
        preference = {"context": context(), "presentation": {"structure": "bullets"}, "created_at": "2026-01-01"}
        result = Provider().generate_draft(context(), "First fact. Second fact.",
                                           {"structure": "auto"}, [], lessons + [preference])
        self.assertEqual(result["text"], "- First fact.\n- Second fact.")

    def test_prior_channel_gate_normalizes_whitespace_without_broadening_channels(self):
        prior = {"is_prior": True, "confirmed": True, "label": "Synthetic guidance",
                 "text": "Keep responses concise.", "context": {"channel": "Team   Chat"}}
        payload = _payload({"channel": " team chat "}, [], [prior])
        self.assertEqual(len(payload["assistant_derived_priors"]), 1)
        self.assertEqual(_payload({"channel": "work"}, [], [prior])["assistant_derived_priors"], [])
        self.assertEqual(_payload({"channel": "team chat"}, [], [{**prior, "confirmed": False}])["assistant_derived_priors"], [])


class MatchingApiIntegrationTests(unittest.TestCase):
    setUp = test_server.ServerTests.setUp
    tearDown = test_server.ServerTests.tearDown
    request = test_server.ServerTests.request

    def save_correction(self):
        declared = {"channel": "work", "audience": "colleague", "relationship": "colleague",
                    "intent": "request review", "stakes": "low"}
        status, saved, _ = self.request("POST", "/api/corrections", {
            "kind": "style", "scope": "similar", "dimension": "diction", "context": declared,
            "original_text": "Please utilize the checklist when reviewing.",
            "revised_text": "Please use the checklist when reviewing.",
            "guidance": "Use plain everyday wording when asking for a review."})
        self.assertEqual(status, 201, saved)
        requested = {"channel": " WORK ", "audience": "coworker", "relationship": "coworker",
                     "intent": "ask for a review", "stakes": "low-stakes"}
        return declared, requested, saved

    def test_review_request_aliases_reach_draft_and_blind_correction_check(self):
        declared, requested, saved = self.save_correction()
        correction, lesson = saved["correction"], saved["lesson"]
        for ident, changes in (("matching", {}), ("reserved", {"held_out": True}),
                               ("other-audience", {"context": {**declared, "audience": "client", "relationship": "client"}})):
            item = self.server.store.add_sample({"text": "A distinct synthetic reference " + ident,
                                                  "context": changes.get("context", declared)})
            self.server.store.update_sample(item["id"], {"status": "confirmed", "held_out": changes.get("held_out", False)})
            if ident == "matching":
                evidence_id = item["id"]
        self.provider.mode = "codex"
        with patch.object(self.provider, "_invoke", return_value={"text": "A synthetic draft.", "warnings": []}) as invoke:
            status, drafted, _ = self.request("POST", "/api/draft", {
                "context": requested, "brief": "Ask for a review of the new Friday agenda."})
            self.assertEqual(status, 200, drafted)
            draft_payload = invoke.call_args.args[1]
            self.assertEqual(drafted["evidence_ids"], [evidence_id])
            self.assertEqual([item["id"] for item in draft_payload["authored_examples"]], [evidence_id])
            self.assertEqual([item["id"] for item in draft_payload["language_lessons"]], [lesson["id"]])
            invoke.reset_mock()
            status, checked, _ = self.request("POST", "/api/corrections/" + correction["id"] + "/checks", {
                "context": requested, "brief": "Ask for a review of next month's launch outline."})
        self.assertEqual(status, 201, checked)
        self.assertNotIn("mapping", checked["case"])
        self.assertNotIn("provenance", checked["case"])
        payloads = [call.args[1] for call in invoke.call_args_list]
        self.assertEqual(len(payloads), 2)
        for field in ("context", "brief", "controls", "authored_examples", "calibration_feedback", "assistant_derived_priors"):
            self.assertEqual(payloads[0][field], payloads[1][field])
        self.assertEqual([item["id"] for item in payloads[0]["authored_examples"]], [evidence_id])
        self.assertEqual(sorted(len(payload["language_lessons"]) for payload in payloads), [0, 1])
        self.assertNotIn(correction["original_text"], json.dumps(payloads))
        current = self.server.corrections.get(correction["id"])
        self.assertEqual(current["context"], correction["context"])
        self.assertEqual(current["lesson_fingerprint"], lesson_fingerprint(lesson))
        status, undone, _ = self.request("POST", "/api/corrections/" + correction["id"] + "/undo")
        self.assertEqual(status, 200, undone)
        self.assertFalse(undone["lesson"]["enabled"])

    def test_equivalent_context_edit_still_blocks_undo_of_changed_lesson(self):
        _, requested, saved = self.save_correction()
        correction, lesson = saved["correction"], saved["lesson"]
        status, changed, _ = self.request("PATCH", "/api/language-lessons/" + lesson["id"], {"context": requested})
        self.assertEqual(status, 200, changed)
        self.assertEqual(matching_lessons([changed["lesson"]], correction["context"]), [changed["lesson"]])
        self.assertNotEqual(lesson_fingerprint(changed["lesson"]), correction["lesson_fingerprint"])
        self.assertEqual(self.server.corrections.get(correction["id"])["status"], "lesson_changed")
        status, _, _ = self.request("POST", "/api/corrections/" + correction["id"] + "/undo")
        self.assertEqual(status, 409)
        current = self.server.language_lessons.list()[0]
        self.assertTrue(current["enabled"])
        self.assertEqual(current["context"]["intent"], "ask for a review")


if __name__ == "__main__":
    unittest.main()
