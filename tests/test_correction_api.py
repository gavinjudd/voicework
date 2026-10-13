import json
import unittest
from unittest.mock import patch

import test_server
from test_corrections import packet


class CorrectionApiTests(unittest.TestCase):
    setUp = test_server.ServerTests.setUp
    tearDown = test_server.ServerTests.tearDown
    request = test_server.ServerTests.request

    def test_correction_save_scope_validation_and_safe_undo(self):
        self.assertEqual(self.request("POST", "/api/corrections", packet(kind="facts"))[0], 400)
        self.assertEqual(self.request("POST", "/api/corrections", packet(context={"channel": "work"}))[0], 400)
        self.assertEqual(self.request("POST", "/api/corrections", packet(), {"X-Voicework-Token": "wrong"})[0], 403)
        status, saved, _ = self.request("POST", "/api/corrections", packet())
        self.assertEqual(status, 201)
        ident = saved["correction"]["id"]
        lesson_id = saved["lesson"]["id"]
        state = self.request("GET", "/api/state")[1]
        self.assertEqual(state["corrections"][0]["id"], ident)
        self.assertEqual(state["samples"], [])
        self.assertEqual(state["feedback"], [])
        self.assertEqual(state["quality_checks"], [])
        self.assertEqual(self.request("POST", "/api/corrections/" + ident + "/checks", {"context": packet()["context"], "brief": packet()["original_text"]})[0], 400)
        self.assertEqual(self.request("POST", "/api/corrections/" + ident + "/checks", {"context": {"channel": "personal"}, "brief": "Another brief"})[0], 400)
        self.request("PATCH", "/api/language-lessons/" + lesson_id, {"guidance": "A later deliberate revision"})
        self.assertEqual(self.request("POST", "/api/corrections/" + ident + "/undo")[0], 409)
        self.assertEqual(self.request("GET", "/api/state")[1]["language_lessons"][0]["guidance"], "A later deliberate revision")
        status, other, _ = self.request("POST", "/api/corrections", packet(scope="channel"))
        self.assertEqual(status, 201)
        undo_route = "/api/corrections/" + other["correction"]["id"] + "/undo"
        self.assertEqual(self.request("POST", undo_route)[1]["correction"]["status"], "undone")
        self.assertEqual(self.request("POST", undo_route)[0], 200)
        current = {item["id"]: item for item in self.request("GET", "/api/state")[1]["language_lessons"]}
        self.assertTrue(current[lesson_id]["enabled"])

    def test_quality_check_is_blind_single_lesson_delta_and_never_training_feedback(self):
        context = packet()["context"]
        saved = self.request("POST", "/api/corrections", packet())[1]
        ident, target_id = saved["correction"]["id"], saved["lesson"]["id"]
        other = self.server.language_lessons.add({"context": context, "dimension": "syntax", "guidance": "Lead with the result."})
        prior = self.server.priors.add({"context": context, "label": "A preference", "text": "Keep replies concise.", "source": "Synthetic test"})
        self.server.priors.confirm(prior["id"])
        sample = self.server.store.add_sample({"text": "Unique approved authored text for this test", "context": context})
        self.server.store.update_sample(sample["id"], {"status": "confirmed"})
        removed = self.server.store.add_sample({"text": "Deleted evidence must not return", "context": context})
        self.server.store.update_sample(removed["id"], {"status": "confirmed"})
        self.server.store.delete_sample(removed["id"])
        reserved = self.server.store.add_sample({"text": "Held-out evidence must not enter this check", "context": context})
        self.server.store.update_sample(reserved["id"], {"status": "confirmed", "held_out": True})
        self.provider.mode = "codex"
        with patch.object(self.provider, "_invoke", return_value={"text": "A synthetic output.", "warnings": []}) as invoke:
            status, response, _ = self.request("POST", "/api/corrections/" + ident + "/checks", {"context": context, "brief": "We will utilize the agenda for the review next week."})
        self.assertEqual(status, 201, response)
        case = response["case"]
        self.assertNotIn("mapping", case)
        self.assertNotIn("provenance", case)
        payloads = [call.args[1] for call in invoke.call_args_list]
        self.assertEqual(len(payloads), 2)
        for field in ("brief", "controls", "context", "authored_examples", "calibration_feedback", "assistant_derived_priors"):
            self.assertEqual(payloads[0][field], payloads[1][field])
        self.assertEqual(payloads[0]["authored_examples"][0]["id"], sample["id"])
        self.assertEqual(len(payloads[0]["authored_examples"]), 1)
        sets = [{item["id"] for item in payload["language_lessons"]} for payload in payloads]
        self.assertIn({other["id"]}, sets)
        self.assertIn({other["id"], target_id}, sets)
        self.assertNotIn(packet()["original_text"], json.dumps(payloads))
        status, rated, _ = self.request("POST", "/api/quality-checks/" + case["id"] + "/rate", {
            "choice": "tie", "meaning": {"a": "preserved", "b": "unsure"}, "effort": {"a": "none", "b": "light"}})
        self.assertEqual(status, 200, rated)
        self.assertIn("mapping", rated["case"])
        self.assertNotIn(sample["text"], json.dumps(rated["case"]["provenance"]))
        state = self.request("GET", "/api/state")[1]
        self.assertEqual(state["feedback"], [])
        self.assertEqual(len(state["samples"]), 2)
        self.assertEqual(len(state["language_lessons"]), 2)
        self.assertEqual(state["quality_summary"]["rated_real"], 1)
        self.server.corrections.undo(ident)
        self.assertEqual(self.request("POST", "/api/corrections/" + ident + "/checks", {"context": context, "brief": "A fresh brief"})[0], 409)


if __name__ == "__main__":
    unittest.main()
