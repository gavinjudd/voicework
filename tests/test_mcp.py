import json
import subprocess
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path

import test_server
from voicework.context import PriorStore
from voicework.language import LanguageLessonStore
from voicework.mcp import TOOLS, VoiceworkMCP, serve
from voicework.proposals import MAX_PENDING, ProposalStore
from voicework.store import Store

ROOT = Path(__file__).resolve().parent.parent


def seed(data):
    path = data / "voicework.sqlite3"
    store = Store(path)
    lessons = LanguageLessonStore(path)
    lessons.add({"context": {"channel": "work", "audience": "teammates"}, "dimension": "diction",
                 "guidance": "Plain verbs.", "preferred": "use", "avoided": "utilize"})
    lessons.add({"context": {"channel": "personal"}, "dimension": "vernacular", "guidance": "lowercase texts"})
    ident = store.add_sample({"title": "Fictional note", "text": "Fictional synthetic sample about a garden shed.", "source_type": "manual",
                              "context": {"channel": "work", "audience": "teammates"}})["id"]
    store.update_sample(ident, {"status": "confirmed"})
    priors = PriorStore(path)
    prior = priors.add({"label": "Background", "text": "Synthetic prior fact.", "context": {"channel": "work"}, "source": "test"})
    priors.confirm(prior["id"])
    return path


class MCPUnitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.path = seed(self.data)
        self.mcp = VoiceworkMCP(self.data)

    def call(self, name, arguments=None, ident=1):
        return self.mcp.handle({"jsonrpc": "2.0", "id": ident, "method": "tools/call", "params": {"name": name, "arguments": arguments or {}}})["result"]

    def test_initialize_and_tools_list(self):
        reply = self.mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                 "params": {"protocolVersion": "2025-03-26", "clientInfo": {"name": "claude-code"}, "capabilities": {}}})
        self.assertEqual(reply["result"]["protocolVersion"], "2025-03-26")
        self.assertIn("tools", reply["result"]["capabilities"])
        unknown = self.mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}})
        self.assertEqual(unknown["result"]["protocolVersion"], "2025-06-18")
        self.assertIsNone(self.mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        names = [tool["name"] for tool in self.mcp.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})["result"]["tools"]]
        self.assertEqual(names, ["list_voice_contexts", "get_voice_guidance", "get_story_voice", "propose_lesson"])
        self.assertEqual(self.mcp.handle({"jsonrpc": "2.0", "id": 4, "method": "nope"})["error"]["code"], -32601)

    def test_guidance_is_context_scoped_and_private_material_is_opt_in(self):
        result = self.call("get_voice_guidance", {"channel": "work", "audience": "teammates"})
        self.assertFalse(result["isError"])
        text = result["content"][0]["text"]
        self.assertIn("Plain verbs.", text)
        self.assertNotIn("lowercase texts", text)
        self.assertNotIn("garden shed", text)
        self.assertNotIn("Synthetic prior fact", text)
        self.assertEqual(result["structuredContent"]["examples"], [])
        full = self.call("get_voice_guidance", {"channel": "work", "audience": "teammates", "include_examples": True, "include_priors": True})
        self.assertIn("garden shed", full["content"][0]["text"])
        self.assertIn("Synthetic prior fact", full["content"][0]["text"])
        # A lesson scoped to teammates does not apply to a different audience.
        other = self.call("get_voice_guidance", {"channel": "work", "audience": "board"})
        self.assertEqual(other["structuredContent"]["language_lessons"], [])

    def test_bad_arguments_return_tool_errors(self):
        self.assertTrue(self.call("get_voice_guidance", {})["isError"])
        self.assertTrue(self.call("get_voice_guidance", {"channel": "work", "include_examples": "yes"})["isError"])
        self.assertTrue(self.call("get_story_voice", {"target": "story", "channels": []})["isError"])
        self.assertEqual(self.mcp.handle({"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": "drop_tables"}})["error"]["code"], -32602)

    def test_story_voice_and_contexts(self):
        contexts = self.call("list_voice_contexts")
        self.assertEqual({c["channel"] for c in contexts["structuredContent"]["channels"]}, {"work", "personal"})
        voice = self.call("get_story_voice", {"target": "spoken interview answer", "channels": ["work"]})
        self.assertEqual(voice["structuredContent"]["format"], "voicework.story-voice")
        self.assertNotIn("lowercase", json.dumps(voice["structuredContent"]))
        self.assertNotIn("Synthetic prior fact", json.dumps(voice["structuredContent"]))
        self.assertEqual(voice["structuredContent"]["version"], 1)
        full = self.call("get_story_voice", {"target": "true story told aloud", "channels": ["work"], "include_facts": True,
                                             "include_examples": True, "topic": "garden"})
        self.assertFalse(full["isError"])
        self.assertEqual(full["structuredContent"]["version"], 2)
        self.assertEqual(full["structuredContent"]["facts"], ["Background: Synthetic prior fact."])
        self.assertEqual(full["structuredContent"]["voice_samples"], [])  # The only excerpt is about a garden shed.
        self.assertIn("1 facts", full["content"][0]["text"])
        self.assertTrue(self.call("get_story_voice", {"target": "story", "channels": ["work"], "include_facts": "yes"})["isError"])
        self.assertTrue(self.call("get_story_voice", {"target": "story", "channels": ["work"], "topic": 5})["isError"])

    def test_proposals_are_pending_and_do_not_change_lessons(self):
        before = LanguageLessonStore(self.path).list()
        result = self.call("propose_lesson", {"context": {"channel": "work"}, "dimension": "rhythm", "guidance": "Shorter openers.",
                                              "before": "In light of the foregoing,", "after": "So,"})
        self.assertFalse(result["isError"])
        self.assertEqual(LanguageLessonStore(self.path).list(), before)
        pending = ProposalStore(self.path).list("pending")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["source"], "MCP: mcp")
        self.assertEqual(pending[0]["after"], "So,")
        self.assertTrue(self.call("propose_lesson", {"context": {"channel": "work"}, "dimension": "rhythm"})["isError"])
        self.assertTrue(self.call("propose_lesson", {"context": {"channel": "work"}, "dimension": "tone", "guidance": "x"})["isError"])

    def test_pending_cap(self):
        store = ProposalStore(self.path)
        for _ in range(MAX_PENDING):
            store.add({"context": {"channel": "work"}, "dimension": "syntax", "guidance": "g"})
        with self.assertRaises(ValueError):
            store.add({"context": {"channel": "work"}, "dimension": "syntax", "guidance": "g"})

    def test_missing_database_is_refused(self):
        with self.assertRaises(ValueError):
            VoiceworkMCP(self.data / "missing")

    def test_transport_handles_garbage_batches_and_notifications(self):
        lines = "\n".join(['not json', json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
                           json.dumps([{"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]), ""])
        out = StringIO()
        serve(self.mcp, StringIO(lines), out)
        replies = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual(replies[0]["error"]["code"], -32700)
        self.assertEqual([r["id"] for r in replies[1]], [1, 2])


class MCPRobustnessTests(unittest.TestCase):
    def test_batch_errors_do_not_kill_the_transport(self):
        class Exploding:
            def handle(self, message):
                if message.get("id") == 2:
                    raise RuntimeError("database is locked")
                return {"jsonrpc": "2.0", "id": message.get("id"), "result": {}}
        out = StringIO()
        batch = [{"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"jsonrpc": "2.0", "id": 2, "method": "ping"}]
        serve(Exploding(), StringIO(json.dumps(batch) + "\n" + json.dumps({"jsonrpc": "2.0", "id": 3, "method": "ping"}) + "\n"), out)
        replies = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual(replies[0][1]["error"]["code"], -32603)
        self.assertEqual(replies[1]["id"], 3)


class MCPStdioTests(unittest.TestCase):
    def test_real_subprocess_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            seed(Path(folder))
            messages = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test"}}},
                        {"jsonrpc": "2.0", "method": "notifications/initialized"},
                        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "get_voice_guidance", "arguments": {"channel": "work", "audience": "teammates"}}}]
            run = subprocess.run([sys.executable, "-m", "voicework.mcp", "--data-dir", folder], cwd=str(ROOT), timeout=30,
                                 input="".join(json.dumps(m) + "\n" for m in messages), capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            replies = [json.loads(line) for line in run.stdout.splitlines()]
            self.assertEqual([r["id"] for r in replies], [1, 2])
            self.assertIn("Plain verbs.", replies[1]["result"]["content"][0]["text"])


class ProposalApiTests(test_server.ServerTests):
    def test_review_queue_accept_and_dismiss(self):
        store = self.server.proposals
        first = store.add({"context": {"channel": "work"}, "dimension": "diction", "guidance": "Say use.", "source": "MCP: test"})
        second = store.add({"context": {"channel": "work"}, "dimension": "syntax", "guidance": "Front-load the ask."})
        state = self.request("GET", "/api/state")[1]
        self.assertEqual({p["id"] for p in state["lesson_proposals"]}, {first["id"], second["id"]})
        self.assertEqual(state["language_lessons"], [])
        status, body, _ = self.request("POST", "/api/lesson-proposals/%s/accept" % first["id"])
        self.assertEqual(status, 200)
        lessons = self.request("GET", "/api/state")[1]["language_lessons"]
        self.assertEqual([(l["id"], l["guidance"], l["enabled"]) for l in lessons], [(body["proposal"]["lesson_id"], "Say use.", True)])
        self.assertEqual(self.request("POST", "/api/lesson-proposals/%s/accept" % first["id"])[0], 400)
        self.assertEqual(self.request("POST", "/api/lesson-proposals/%s/dismiss" % second["id"])[0], 200)
        self.assertEqual(self.request("GET", "/api/state")[1]["lesson_proposals"], [])
        self.assertEqual(len(self.request("GET", "/api/state")[1]["language_lessons"]), 1)
        self.assertEqual(self.request("POST", "/api/lesson-proposals/missing/accept")[0], 404)
        self.assertEqual(self.request("POST", "/api/lesson-proposals/%s/approve" % second["id"])[0], 404)


if __name__ == "__main__":
    unittest.main()
