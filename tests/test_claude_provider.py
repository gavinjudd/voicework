import json
import os
import sqlite3
import stat
import tempfile
import textwrap
import unittest
from pathlib import Path

from voicework.evaluation import EvaluationStore
from voicework.migrations import allow_live_provider
from voicework.provider import Provider, is_live

FAKE = textwrap.dedent('''\
    #!/usr/bin/env python3
    import json, os, sys
    record = os.environ["FAKE_CLAUDE_RECORD"]
    prompt = sys.stdin.read()
    json.dump({"argv": sys.argv[1:], "stdin": prompt, "cwd": os.getcwd()}, open(record, "w"))
    mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
    if mode == "ok":
        out = {"type": "result", "subtype": "success", "is_error": False, "result": "",
               "structured_output": json.loads(os.environ["FAKE_CLAUDE_OUTPUT"])}
    elif mode == "login":
        out = {"type": "result", "subtype": "success", "is_error": True, "result": "Not logged in · Please run /login SECRET-DRAFT", "terminal_reason": "api_error"}
    elif mode == "expired":
        out = {"type": "result", "subtype": "success", "is_error": True, "result": "Failed to authenticate: OAuth session expired and could not be refreshed", "terminal_reason": "api_error", "api_error_status": None}
    elif mode == "oldcli":
        out = {"type": "result", "subtype": "success", "is_error": True, "api_error_status": 400, "terminal_reason": "api_error",
               "result": "API Error: 400 Claude Code 2.1.266 does not support this model; version 2.1.280 or newer is required. Run 'claude update', or update the Claude desktop app, then try again."}
    elif mode == "schema":
        out = {"type": "result", "subtype": "error_max_structured_output_retries", "is_error": True, "result": "SECRET-DRAFT"}
    elif mode == "limit":
        out = {"type": "result", "subtype": "success", "is_error": True, "result": "Claude usage limit reached", "api_error_status": 429}
    elif mode == "nostruct":
        out = {"type": "result", "subtype": "success", "is_error": False, "result": "plain text"}
    else:
        print("Error: unknown option --safe-mode", file=sys.stderr); sys.exit(1)
    print(json.dumps(out))
''')


class ClaudeProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.exe = Path(self.temp.name) / "claude"
        self.exe.write_text(FAKE)
        self.exe.chmod(self.exe.stat().st_mode | stat.S_IEXEC)
        self.record = Path(self.temp.name) / "record.json"
        self.env = {"FAKE_CLAUDE_RECORD": str(self.record), "FAKE_CLAUDE_MODE": "ok",
                    "FAKE_CLAUDE_OUTPUT": json.dumps({"text": "Ready Friday.", "warnings": []})}
        self.saved = {key: os.environ.get(key) for key in self.env}
        os.environ.update(self.env)
        self.addCleanup(self.restore)

    def restore(self):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def provider(self, **kwargs):
        return Provider("claude", executable=str(self.exe), **kwargs)

    def test_draft_runs_locked_down_cli_and_records_provider(self):
        sample = {"id": "s1", "text": "Fictional sample.", "status": "confirmed", "context": {"channel": "work"}}
        result = self.provider().generate_draft({"channel": "work"}, "The report is ready Friday.", {}, [sample], [])
        self.assertEqual((result["provider"], result["text"], result["evidence_ids"]), ("claude", "Ready Friday.", ["s1"]))
        call = json.loads(self.record.read_text())
        argv = call["argv"]
        for flag in ("-p", "--safe-mode", "--no-session-persistence", "--strict-mcp-config", "--disable-slash-commands"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertEqual(argv[argv.index("--model") + 1], "claude-opus-5-5")
        self.assertEqual(argv[argv.index("--output-format") + 1], "json")
        self.assertEqual(json.loads(argv[argv.index("--json-schema") + 1])["required"], ["warnings", "text"])
        self.assertIn("UNTRUSTED TASK DATA", call["stdin"])  # prompt on stdin, not argv
        self.assertNotIn("UNTRUSTED TASK DATA", " ".join(argv))
        self.assertIn("voicework-claude-", call["cwd"])  # empty temp directory, not the repo

    def test_round_document_and_context_paths(self):
        os.environ["FAKE_CLAUDE_OUTPUT"] = json.dumps({"options": [{"id": "x", "text": "One"}, {"id": "y", "text": "Two"}], "warnings": []})
        result = self.provider(model="claude-sonnet-5-5").generate_round({"channel": "work"}, [], [], focus="rhythm")
        self.assertEqual([o["id"] for o in result["options"]], ["a", "b"])
        self.assertEqual(result["provider"], "claude")
        os.environ["FAKE_CLAUDE_OUTPUT"] = json.dumps({"context": {"channel": "work", "audience": "team", "intent": "update", "stakes": "low", "relationship": "peer", "scenario": "status"},
                                                       "rationale": ["greeting"], "uncertainties": ["short"], "authorship_signals": ["unverified"]})
        from voicework.context import infer_context
        self.assertEqual(infer_context({"text": "Fictional note.", "status": "pending"}, self.provider())["provider"], "claude")

    def test_failures_are_classified_without_echoing_output(self):
        for mode, phrase in (("login", "not signed in"), ("expired", "claude auth login"), ("oldcli", "needs 2.1.280 or newer"), ("schema", "schema"), ("limit", "usage limit"),
                             ("nostruct", "structured result"), ("badflag", "--safe-mode")):
            os.environ["FAKE_CLAUDE_MODE"] = mode
            with self.assertRaises(ValueError) as caught:
                self.provider().generate_draft({}, "Text", {}, [], [])
            self.assertIn(phrase, str(caught.exception))
            self.assertNotIn("SECRET", str(caught.exception))

    def test_invalid_structured_result_is_rejected(self):
        os.environ["FAKE_CLAUDE_OUTPUT"] = json.dumps({"text": "", "warnings": []})
        with self.assertRaises(ValueError):
            self.provider().generate_draft({}, "Text", {}, [], [])

    def test_missing_cli_and_executable_resolution(self):
        missing = Provider("claude", executable=str(Path(self.temp.name) / "nope"))
        self.assertFalse(missing.available())
        with self.assertRaisesRegex(ValueError, "Claude Code CLI was not found"):
            missing.generate_draft({}, "Text", {}, [], [])
        os.environ["VOICEWORK_CLAUDE_BIN"] = str(self.exe)
        try:
            self.assertEqual(Provider("claude").executable, str(self.exe))
            self.assertTrue(Provider("claude").available())
        finally:
            del os.environ["VOICEWORK_CLAUDE_BIN"]
        self.assertTrue(is_live("claude"))

    def test_evaluation_cases_store_claude(self):
        os.environ["FAKE_CLAUDE_OUTPUT"] = json.dumps({"text": "Draft.", "warnings": []})
        path = Path(self.temp.name) / "voicework.sqlite3"
        case = EvaluationStore(path).create_case({"channel": "work"}, "Brief.", self.provider(), [], [])
        self.assertEqual(case["provider"], "claude")


class ClaudeMigrationTests(unittest.TestCase):
    def test_widens_both_older_constraints(self):
        for old in ("('offline','codex')", "('offline','codex','anthropic')"):
            conn = sqlite3.connect(":memory:")
            conn.execute("CREATE TABLE eval_cases (id TEXT PRIMARY KEY, provider TEXT NOT NULL CHECK(provider IN %s))" % old)
            conn.execute("INSERT INTO eval_cases VALUES ('x','codex')")
            conn.commit()
            with conn:
                self.assertTrue(allow_live_provider(conn, "eval_cases"))
            conn.execute("INSERT INTO eval_cases VALUES ('y','claude')")
            self.assertEqual(conn.execute("SELECT provider FROM eval_cases ORDER BY id").fetchall(), [("codex",), ("claude",)])
            with conn:
                self.assertFalse(allow_live_provider(conn, "eval_cases"))


if __name__ == "__main__":
    unittest.main()
