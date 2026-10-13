import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from voicework import harness


class HarnessTests(unittest.TestCase):
    def test_client_fetches_only_session_and_job_not_raw_profile(self):
        request = {"source": "A synthetic document.", "context": {"channel": "technical article"}}
        result = {"text": "A rewritten synthetic document.", "checks": {"status": "checked"}}
        responses = [{"token": "temporary-session"}, {"id": "job-one", "status": "pending"},
                     {"id": "job-one", "status": "running"},
                     {"id": "job-one", "status": "complete", "result": result}]
        with patch.object(harness, "request_json", side_effect=responses) as call, patch.object(harness.time, "sleep"):
            actual = harness.run(request, "http://127.0.0.1:9876")
        self.assertEqual(actual, result)
        self.assertEqual([item.args[1] for item in call.call_args_list],
                         ["/api/session", "/api/documents", "/api/documents/job-one", "/api/documents/job-one"])
        self.assertEqual(call.call_args_list[1].args, (("127.0.0.1", 9876), "/api/documents", request, "temporary-session"))
        self.assertNotIn("/api/state", str(call.call_args_list))

    def test_check_does_not_request_a_rewrite(self):
        request = {"source": "Original text.", "text": "Edited text."}
        result = {"checks": {"status": "review_required"}}
        with patch.object(harness, "request_json", side_effect=[{"token": "temporary"},
                          {"id": "check-one", "status": "complete", "result": result}]) as call:
            self.assertEqual(harness.run(request, check=True), result)
        self.assertEqual(call.call_args_list[1].args[1], "/api/document-checks")

    def test_timeout_keeps_job_identifier_and_does_not_claim_cancellation(self):
        with patch.object(harness, "request_json", side_effect=[{"token": "temporary"},
                          {"id": "pending-job", "status": "running"}]) as call, \
                patch.object(harness.time, "monotonic", side_effect=[0, 61]):
            with self.assertRaisesRegex(ValueError, "may still be running.*pending-job"):
                harness.run({"source": "Synthetic."}, timeout=60)
        self.assertEqual(call.call_count, 2)

    def test_failed_job_never_returns_partial_text(self):
        with patch.object(harness, "request_json", side_effect=[{"token": "temporary"},
                          {"id": "failed-job", "status": "failed", "error": "Document job failed."}]):
            with self.assertRaisesRegex(ValueError, "Document job failed"):
                harness.run({"source": "Synthetic."})

    def test_remote_or_credentialed_origin_is_rejected_before_network(self):
        urls = ["https://127.0.0.1:8765", "http://example.com", "http://127.0.0.1.evil.test",
                "http://name:password@localhost:8765", "http://localhost:8765/path",
                "http://localhost:8765?query=1", "http://localhost:8765/#fragment",
                "file:///tmp/request.json"]
        with patch.object(harness, "request_json") as call:
            for url in urls:
                with self.subTest(url=url), self.assertRaises(ValueError):
                    harness.run({"source": "Synthetic."}, url)
            call.assert_not_called()

    def test_http_redirect_is_not_followed_and_connection_is_closed(self):
        response = Mock(status=302)
        response.read.return_value = b'{"error":"Redirect refused"}'
        connection = Mock()
        connection.getresponse.return_value = response
        with patch.object(harness.http.client, "HTTPConnection", return_value=connection) as factory:
            with self.assertRaisesRegex(ValueError, "Redirect refused"):
                harness.request_json(("127.0.0.1", 8765), "/api/session")
        factory.assert_called_once()
        connection.request.assert_called_once()
        connection.close.assert_called_once()

    def test_output_is_private_and_existing_file_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            request_path = Path(directory) / "request.json"
            output_path = Path(directory) / "result.json"
            request_path.write_text(json.dumps({"source": "Synthetic text."}), encoding="utf-8")
            result = {"text": "Synthetic result.", "checks": {"status": "review_required"}}
            arguments = ["--request", str(request_path), "--output", str(output_path)]
            with patch.object(harness, "run", return_value=result) as run:
                self.assertEqual(harness.main(arguments), 3)
                self.assertEqual(json.loads(output_path.read_text()), result)
                self.assertEqual(os.stat(output_path).st_mode & 0o777, 0o600)
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as stopped:
                    harness.main(arguments)
                self.assertEqual(stopped.exception.code, 2)
                self.assertEqual(run.call_count, 1)
            self.assertEqual(json.loads(output_path.read_text()), result)
            self.assertEqual(json.loads(request_path.read_text()), {"source": "Synthetic text."})

    def test_checked_result_has_success_exit(self):
        result = {"text": "Synthetic result.", "checks": {"status": "checked"}}
        output = io.StringIO()
        with patch.object(harness.sys, "stdin", io.StringIO('{"source":"Synthetic."}')), \
                patch.object(harness, "run", return_value=result), contextlib.redirect_stdout(output):
            self.assertEqual(harness.main(["--request", "-"]), 0)
        self.assertEqual(json.loads(output.getvalue()), result)


if __name__ == "__main__":
    unittest.main()
