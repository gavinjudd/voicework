import http.client
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_server
from test_corrections import packet


class ConfidenceApiTests(unittest.TestCase):
    setUp = test_server.ServerTests.setUp
    tearDown = test_server.ServerTests.tearDown
    request = test_server.ServerTests.request

    def test_state_summary_recomputes_after_evidence_review_and_holdout_changes(self):
        initial = self.request("GET", "/api/state")[1]
        self.assertEqual(initial["voice_confidence"]["completed"], 0)
        sample = self.request("POST", "/api/samples", {"text": "Synthetic authored evidence"})[1]["sample"]
        route = "/api/samples/" + sample["id"]
        self.assertEqual(self.request("GET", "/api/state")[1]["voice_confidence"]["completed"], 0)
        self.request("PATCH", route, {"status": "confirmed"})
        confirmed = self.request("GET", "/api/state")[1]
        self.assertEqual(confirmed["voice_confidence"]["evidence"]["authored_samples"], 1)
        self.assertEqual(confirmed["voice_confidence"]["completed"], 1)
        self.assertEqual(confirmed["profile"]["training_count"], 1)
        self.request("PATCH", route, {"held_out": True})
        with patch.object(self.provider, "generate_draft") as generation:
            held_out = self.request("GET", "/api/state")[1]
        generation.assert_not_called()
        self.assertEqual(held_out["voice_confidence"]["completed"], 0)
        self.assertEqual(held_out["voice_confidence"]["evidence"]["authored_samples"], 0)
        self.assertNotIn(sample["text"], json.dumps(held_out["voice_confidence"]))

    def test_rated_codex_comparisons_feed_readiness_without_changing_profile(self):
        self.request("POST", "/api/language-lessons", {"dimension": "syntax", "guidance": "Keep the message direct."})
        original_profile = self.request("GET", "/api/state")[1]["profile"]
        self.provider.mode = "codex"
        cases = [("First situation.", "Work"), ("Second situation.", "personal"), ("Third situation.", "work")]
        with patch.object(self.provider, "_invoke", return_value={"text": "Synthetic candidate.", "warnings": ["Private candidate warning"]}), \
                patch("voicework.evaluation.secrets.choice", return_value=False):
            for index, (brief, channel) in enumerate(cases):
                status, response, _ = self.request("POST", "/api/evaluations", {"brief": brief, "context": {"channel": channel}})
                self.assertEqual(status, 201)
                case_id = response["case"]["id"]
                before = self.request("GET", "/api/state")[1]
                self.assertEqual(before["voice_confidence"]["validation"]["rated_real"], index)
                pending = next(item for item in before["evaluations"] if item["id"] == case_id)
                self.assertNotIn("mapping", pending)
                self.assertNotIn("Private candidate warning", json.dumps(pending))
                self.assertNotIn(brief, json.dumps(before["voice_confidence"]))
                self.assertEqual(self.request("POST", "/api/evaluations/" + case_id + "/rate", {"choice": "a", "factual_issue": True})[0], 200)
        final = self.request("GET", "/api/state")[1]
        self.assertEqual(final["profile"], original_profile)
        self.assertEqual(final["voice_confidence"]["completed"], 3)
        self.assertEqual(final["voice_confidence"]["validation"]["baseline_wins"], 3)
        self.assertEqual(final["voice_confidence"]["validation"]["calibrated_wins"], 0)
        self.assertEqual(final["voice_confidence"]["validation"]["factual_flags"], 3)
        self.assertEqual(final["voice_confidence"]["validation"]["channels"], ["personal", "work"])
        self.assertEqual(final["feedback"], [])
        self.assertEqual(final["samples"], [])

    def test_offline_ratings_remain_rehearsal(self):
        self.request("POST", "/api/language-lessons", {"dimension": "diction", "preferred": "Let's review."})
        status, response, _ = self.request("POST", "/api/evaluations", {"brief": "A synthetic brief.", "context": {"channel": "work"}})
        self.assertEqual(status, 201)
        self.request("POST", "/api/evaluations/" + response["case"]["id"] + "/rate", {"choice": "a"})
        state = self.request("GET", "/api/state")[1]
        self.assertEqual(state["evaluation_summary"]["rated_offline"], 1)
        self.assertEqual(state["voice_confidence"]["completed"], 1)
        self.assertEqual(state["voice_confidence"]["validation"]["rated_real"], 0)

    def test_rated_correction_check_remains_separate_and_undo_removes_active_guidance(self):
        saved = self.request("POST", "/api/corrections", packet())[1]
        correction_id = saved["correction"]["id"]
        self.provider.mode = "codex"
        with patch.object(self.provider, "_invoke", return_value={"text": "Synthetic quality candidate.", "warnings": []}):
            status, response, _ = self.request("POST", "/api/corrections/" + correction_id + "/checks",
                                               {"context": packet()["context"], "brief": "A new synthetic situation for checking the wording."})
        self.assertEqual(status, 201, response)
        check_id = response["case"]["id"]
        self.assertEqual(self.request("GET", "/api/state")[1]["voice_confidence"]["validation"]["correction_checks"], 0)
        self.request("POST", "/api/quality-checks/" + check_id + "/rate", {
            "choice": "tie", "meaning": {"a": "preserved", "b": "preserved"}, "effort": {"a": "none", "b": "light"}})
        state = self.request("GET", "/api/state")[1]
        self.assertEqual(state["voice_confidence"]["completed"], 1)
        self.assertEqual(state["voice_confidence"]["validation"]["correction_checks"], 1)
        self.assertEqual(state["voice_confidence"]["validation"]["rated_real"], 0)
        self.assertEqual(state["voice_confidence"]["evidence"]["authored_samples"], 0)
        self.request("POST", "/api/corrections/" + correction_id + "/undo")
        undone = self.request("GET", "/api/state")[1]["voice_confidence"]
        self.assertEqual(undone["completed"], 0)
        self.assertEqual(undone["evidence"]["lessons"], 0)
        self.assertEqual(undone["validation"]["correction_checks"], 1)

    def test_confidence_module_is_served_as_javascript(self):
        web_root = Path(self.temp.name) / "web"
        web_root.mkdir()
        content = "export const confidence = true;"
        (web_root / "voice-confidence.mjs").write_text(content)
        self.server.web_root = web_root
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request("GET", "/voice-confidence.mjs")
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.getheader("Content-Type"), "text/javascript; charset=utf-8")
            self.assertEqual(response.read().decode(), content)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
