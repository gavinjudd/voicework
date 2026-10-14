import json
import os
import sqlite3
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from voicework.evaluation import EvaluationStore
from voicework.migrations import allow_live_provider
from voicework.provider import Provider, is_live
from voicework.quality import QualityStore


class FakeAnthropic(BaseHTTPRequestHandler):
    responses = []
    requests = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        type(self).requests.append({"headers": {k.lower(): v for k, v in self.headers.items()}, "body": body, "path": self.path})
        status, payload = type(self).responses.pop(0)
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def tool_message(result, stop="tool_use"):
    return {"type": "message", "stop_reason": stop,
            "content": [{"type": "text", "text": "ignored"}, {"type": "tool_use", "id": "t1", "name": "voicework_result", "input": result}]}


class AnthropicProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), FakeAnthropic)
        cls.url = "http://127.0.0.1:%d/v1/messages" % cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        FakeAnthropic.responses, FakeAnthropic.requests = [], []
        self.env = patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test-synthetic"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def provider(self, **kwargs):
        return Provider("anthropic", endpoint=self.url, **kwargs)

    def test_draft_uses_forced_structured_tool_and_records_exact_provider(self):
        FakeAnthropic.responses.append((200, tool_message({"text": "Ready by Friday.", "warnings": ["thin evidence"]})))
        sample = {"id": "s1", "text": "Fictional sample.", "status": "confirmed", "context": {"channel": "work"}}
        result = self.provider().generate_draft({"channel": "work"}, "The report is ready Friday.", {"polish": 50}, [sample], [])
        self.assertEqual(result["provider"], "anthropic")
        self.assertEqual(result["text"], "Ready by Friday.")
        request = FakeAnthropic.requests[0]
        self.assertEqual(request["headers"]["x-api-key"], "sk-test-synthetic")
        self.assertEqual(request["headers"]["anthropic-version"], "2023-06-01")
        body = request["body"]
        self.assertEqual(body["model"], "claude-opus-5-5")
        self.assertEqual(body["tool_choice"], {"type": "tool", "name": "voicework_result"})
        self.assertEqual([tool["name"] for tool in body["tools"]], ["voicework_result"])
        self.assertEqual(body["tools"][0]["input_schema"]["required"], ["warnings", "text"])
        self.assertIn("UNTRUSTED TASK DATA", body["messages"][0]["content"])

    def test_round_is_validated_and_relabelled(self):
        FakeAnthropic.responses.append((200, tool_message({"options": [{"id": "x", "text": "One"}, {"id": "y", "text": "Two"}], "warnings": []})))
        result = self.provider(model="claude-sonnet-5-5").generate_round({"channel": "work"}, [], [], focus="diction")
        self.assertEqual([o["id"] for o in result["options"]], ["a", "b"])
        self.assertEqual(FakeAnthropic.requests[0]["body"]["model"], "claude-sonnet-5-5")
        FakeAnthropic.responses.append((200, tool_message({"options": [{"id": "a", "text": "Only one"}], "warnings": []})))
        with self.assertRaisesRegex(ValueError, "Anthropic API returned an invalid comparison"):
            self.provider().generate_round({"channel": "work"}, [], [])

    def test_http_failures_never_echo_response_body(self):
        for status, phrase in ((401, "API key"), (404, "model"), (429, "rate-limited"), (529, "overloaded"), (400, "invalid")):
            FakeAnthropic.responses.append((status, {"error": {"message": "SECRET PROMPT FRAGMENT"}}))
            with self.assertRaises(ValueError) as caught:
                self.provider().generate_draft({}, "Text", {}, [], [])
            self.assertIn(phrase, str(caught.exception))
            self.assertNotIn("SECRET", str(caught.exception))

    def test_truncated_missing_or_malformed_results_are_rejected(self):
        cases = [tool_message({"text": "partial", "warnings": []}, stop="max_tokens"),
                 {"type": "message", "stop_reason": "end_turn", "content": [{"type": "text", "text": "{\"text\": \"x\"}"}]},
                 tool_message({"text": "ok", "warnings": "not a list"}),
                 tool_message({"text": "", "warnings": []})]
        for message in cases:
            FakeAnthropic.responses.append((200, message))
            with self.assertRaises(ValueError):
                self.provider().generate_draft({}, "Text", {}, [], [])

    def test_missing_key_fails_without_request_and_without_fallback(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            provider = self.provider()
            self.assertFalse(provider.available())
            with self.assertRaisesRegex(ValueError, "ANTHROPIC_API_KEY"):
                provider.generate_draft({}, "Text", {}, [], [])
        self.assertEqual(FakeAnthropic.requests, [])

    def test_endpoint_must_be_https_except_loopback(self):
        for url in ("http://example.com/v1/messages", "https://user:pw@example.com/x", "https://example.com/x?key=1", "ftp://example.com"):
            with self.assertRaises(ValueError):
                Provider("anthropic", endpoint=url)
        self.assertEqual(Provider("anthropic").endpoint, "https://api.anthropic.com/v1/messages")
        self.assertTrue(is_live("anthropic") and is_live("codex") and not is_live("offline"))
        with self.assertRaises(ValueError):
            Provider("openai")

    def test_context_inference_records_anthropic(self):
        from voicework.context import infer_context
        FakeAnthropic.responses.append((200, tool_message({
            "context": {"channel": "work", "audience": "team", "intent": "update", "stakes": "low", "relationship": "peer", "scenario": "status"},
            "rationale": ["greeting"], "uncertainties": ["short"], "authorship_signals": ["unverified"]})))
        result = infer_context({"text": "Fictional status note.", "status": "pending"}, self.provider())
        self.assertEqual(result["provider"], "anthropic")


class ProviderMigrationTests(unittest.TestCase):
    OLD_EVAL = """CREATE TABLE eval_cases (
                id TEXT PRIMARY KEY,
                context TEXT NOT NULL, brief TEXT NOT NULL, options TEXT NOT NULL,
                provider TEXT NOT NULL CHECK(provider IN ('offline','codex')),
                model TEXT NOT NULL, controls TEXT NOT NULL,
                mapping TEXT NOT NULL, provider_warnings TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'unrated' CHECK(status IN ('unrated','rated')),
                created_at TEXT NOT NULL, choice TEXT,
                factual_issue INTEGER, notes TEXT, rated_at TEXT
            )"""

    def test_existing_rows_survive_and_anthropic_becomes_storable(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "voicework.sqlite3"
            conn = sqlite3.connect(str(path))
            conn.execute(self.OLD_EVAL)
            rows = [("e%d" % i, "{}", "brief %d" % i, "[]", ("codex", "offline")[i % 2], "m", "{}", "{}", "{}", "rated", "2026-10-0%d" % (i + 1), "a", 0, "note", "t") for i in range(3)]
            conn.executemany("INSERT INTO eval_cases VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            conn.commit()
            before = conn.execute("SELECT * FROM eval_cases ORDER BY id").fetchall()
            conn.close()
            EvaluationStore(path)
            EvaluationStore(path)  # second open is a no-op
            QualityStore(path)
            conn = sqlite3.connect(str(path))
            self.assertEqual(conn.execute("SELECT * FROM eval_cases ORDER BY id").fetchall(), before)
            sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='eval_cases'").fetchone()[0]
            self.assertIn("'anthropic'", sql)
            conn.execute("INSERT INTO eval_cases VALUES ('new','{}','b','[]','anthropic','m','{}','{}','{}','unrated','t',NULL,NULL,NULL,NULL)")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO eval_cases VALUES ('bad','{}','b','[]','other','m','{}','{}','{}','unrated','t',NULL,NULL,NULL,NULL)")
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertFalse(allow_live_provider(conn, "eval_cases"))
            conn.close()

    def test_quoted_schema_after_migration_and_open_transaction(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(self.OLD_EVAL.replace("CREATE TABLE eval_cases", 'CREATE TABLE "eval_cases"'))
        conn.execute("INSERT INTO eval_cases VALUES ('x','{}','b','[]','codex','m','{}','{}','{}','unrated','t',NULL,NULL,NULL,NULL)")
        with conn:
            self.assertTrue(allow_live_provider(conn, "eval_cases"))
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM eval_cases").fetchone()[0], 1)
        self.assertIn("'anthropic'", conn.execute("SELECT sql FROM sqlite_master WHERE name='eval_cases'").fetchone()[0])

    def test_rejects_unsafe_table_names(self):
        with self.assertRaises(ValueError):
            allow_live_provider(sqlite3.connect(":memory:"), "x; DROP TABLE y")


if __name__ == "__main__":
    unittest.main()
