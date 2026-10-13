import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from voicework.provider import Provider
from voicework.server import make_server


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.provider = Provider(mode="offline")
        self.server = make_server(port=0, data_dir=Path(self.temp.name) / "data", provider=self.provider)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        self.token = self.request("GET", "/api/state")[1]["token"]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.temp.cleanup()

    def request(self, method, path, body=None, headers=None, raw=None):
        final_headers = {}
        if method not in {"GET", "HEAD"}:
            final_headers = {"Content-Type": "application/json", "X-Voicework-Token": getattr(self, "token", "")}
            body = json.dumps(body if body is not None else {}) if raw is None else raw
        final_headers.update(headers or {})
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request(method, path, body=body, headers=final_headers)
            response = conn.getresponse()
            raw_response = response.read()
            return response.status, json.loads(raw_response) if raw_response else {}, dict(response.getheaders())
        finally:
            conn.close()

    def test_empty_state_and_full_persisted_flow(self):
        status, state, _ = self.request("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(state["samples"], [])
        self.assertEqual(state["profile"]["xp"], 0)
        status, response, _ = self.request("POST", "/api/samples", {"title": "Work note", "text": "Real authored example", "context": {"channel": "work"}})
        self.assertEqual(status, 201)
        sample_id = response["sample"]["id"]
        self.assertEqual(response["sample"]["status"], "pending")
        self.assertEqual(self.request("PATCH", "/api/samples/" + sample_id, {"status": "confirmed"})[0], 200)
        status, response, _ = self.request("POST", "/api/rounds", {"context": {"channel": "work", "scenario": "Thank a teammate"}})
        self.assertEqual(status, 201)
        round_ = response["round"]
        self.assertEqual(round_["evidence_ids"], [sample_id])
        status, response, _ = self.request("POST", "/api/feedback", {"round_id": round_["id"], "choice": "a", "notes": "Use concise prose", "presentation": {"structure": "prose"}})
        self.assertEqual(status, 201)
        self.assertEqual(response["profile"]["feedback_count"], 1)
        self.assertEqual(self.request("POST", "/api/feedback", {"round_id": round_["id"], "choice": "b"})[0], 400)
        status, drafted, _ = self.request("POST", "/api/draft", {"context": {"channel": "work"}, "brief": "The meeting is at 3 pm.", "controls": {"structure": "prose"}})
        self.assertEqual(status, 200)
        self.assertEqual(drafted["evidence_ids"], [sample_id])
        self.assertNotIn("Real authored example", self.request("GET", "/api/export")[1]["markdown"])
        self.assertIn("Real authored example", self.request("GET", "/api/export?examples=1")[1]["markdown"])
        self.assertEqual(self.request("DELETE", "/api/samples/" + sample_id)[0], 200)
        current = self.request("GET", "/api/state")[1]
        self.assertEqual(current["samples"], [])
        self.assertEqual(current["rounds"][0]["evidence_ids"], [])
        self.assertEqual(current["feedback"][0]["notes"], "Use concise prose")

    def test_empty_rehearsal_and_holdout_exclusion(self):
        self.assertEqual(self.request("POST", "/api/rounds", {"context": {"channel": "personal"}})[0], 201)
        sample = self.server.store.add_sample({"text": "Reserved example"})
        self.server.store.update_sample(sample["id"], {"status": "confirmed", "held_out": True})
        response = self.request("POST", "/api/draft", {"brief": "Hello", "context": {"channel": "work"}})[1]
        self.assertEqual(response["evidence_ids"], [])

    def test_token_origin_host_and_content_type(self):
        payload = {"text": "Must not be stored"}
        attempts = [
            {"X-Voicework-Token": "wrong"},
            {"Origin": "https://evil.test"},
            {"Origin": "null"},
            {"Host": "evil.test:%d" % self.port},
            {"Sec-Fetch-Site": "cross-site"},
        ]
        for headers in attempts:
            with self.subTest(headers=headers):
                self.assertEqual(self.request("POST", "/api/samples", payload, headers)[0], 403)
        self.assertEqual(self.request("GET", "/api/state", headers={"Origin": "https://evil.test"})[0], 403)
        self.assertEqual(self.request("POST", "/api/samples", payload, {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.server.store.samples(), [])
        origin = "http://127.0.0.1:%d" % self.port
        self.assertEqual(self.request("POST", "/api/samples", {"text": "Valid"}, {"Origin": origin})[0], 201)

    def test_malformed_json_and_invalid_import_are_non_destructive(self):
        for raw in ('{"text":', '[]', '{"text":"first","text":"second"}', '{"value":NaN}', ''):
            with self.subTest(raw=raw):
                self.assertEqual(self.request("POST", "/api/samples", raw=raw)[0], 400)
        self.assertEqual(self.request("POST", "/api/import", {"items": [{"text": "Valid part"}, {"text": ""}]})[0], 400)
        self.assertEqual(self.server.store.samples(), [])
        self.assertEqual(self.request("POST", "/api/draft", {"brief": "Hello", "controls": {"warmth": 200}})[0], 400)

    def test_discovery_and_dedup_through_api(self):
        documents = Path(self.temp.name).resolve() / "documents"
        documents.mkdir()
        (documents / "example.txt").write_text("Discovered candidate", encoding="utf-8")
        response = self.request("POST", "/api/discover", {"roots": [str(documents)]})
        self.assertEqual(response[0], 200)
        self.assertEqual(response[1]["added"], 1)
        repeat = self.request("POST", "/api/discover", {"roots": [str(documents)]})[1]
        self.assertEqual((repeat["added"], repeat["duplicates"]), (0, 1))
        self.assertEqual(self.server.store.samples()[0]["status"], "pending")

    def test_no_static_path_escape_and_browser_protections(self):
        for path in ("/../voicework/store.py", "/%2e%2e/voicework/store.py", "/data/voicework.sqlite3"):
            self.assertEqual(self.request("GET", path)[0], 404)
        _, _, headers = self.request("GET", "/api/state")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        with self.assertRaises(ValueError):
            make_server(host="0.0.0.0", data_dir=self.temp.name)


if __name__ == "__main__":
    unittest.main()
