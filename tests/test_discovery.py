import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from voicework.discovery import discover, extract


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_bounded_scan_skips_symlinks_hidden_credentials_and_configs(self):
        self.write("letter.txt", "This is a candidate for review.")
        self.write(".hidden.md", "Hidden")
        self.write("node_modules/note.txt", "Dependency")
        self.write("passwords.txt", "private")
        self.write("credentials.json", '{"items":[{"text":"Never import me"}]}')
        self.write("other.json", '{"setting":"Not prose"}')
        self.write("ordinary.txt", "api_key = sk-" + "X" * 30)
        (self.root / "alias.txt").symlink_to(self.root / "letter.txt")
        result = discover([str(self.root)])
        self.assertEqual([item["text"] for item in result["items"]], ["This is a candidate for review."])
        self.assertGreaterEqual(result["skipped"], 6)
        self.assertTrue(any("Credential-like" in error for error in result["errors"]))
        self.assertNotIn("X" * 30, json.dumps(result))

    def test_scan_limits_and_excluded_data(self):
        for index in range(4):
            self.write("note%d.txt" % index, "Candidate %d" % index)
        self.write("data/private.md", "App data")
        result = discover([str(self.root)], 2, [self.root / "data"])
        self.assertEqual(len(result["items"]), 2)
        self.assertTrue(result["limited"])
        with self.assertRaises(ValueError):
            discover(["relative/path"])
        with self.assertRaises(ValueError):
            discover([str(self.root)], True)

    def test_controlled_json_import_and_user_only_chatgpt_active_branch(self):
        self.write("samples.json", json.dumps({"items": [{"title": "Imported", "text": "Explicit item"}]}))
        conversation = {"title": "A conversation", "current_node": "new", "mapping": {
            "root": {"parent": None, "message": {"author": {"role": "assistant"}, "content": {"content_type": "text", "parts": ["Never assistant evidence"]}}},
            "old": {"parent": "root", "message": {"author": {"role": "user"}, "content": {"content_type": "text", "parts": ["Obsolete edit"]}}},
            "new": {"parent": "root", "message": {"author": {"role": "user"}, "content": {"content_type": "text", "parts": ["User contribution"]}}},
        }}
        self.write("conversations.json", json.dumps([conversation]))
        result = discover([str(self.root)])
        self.assertEqual({i["text"] for i in result["items"]}, {"Explicit item", "User contribution"})

    def test_docx_and_eml_use_only_document_text(self):
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, "w") as archive:
            archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Hello document</w:t></w:r></w:p></w:body></w:document>')
        self.assertEqual(extract(Path("sample.docx"), raw.getvalue())[0]["text"], "Hello document")
        email = b"From: friend@example.test\nTo: me@example.test\nSubject: Title\nContent-Type: text/plain; charset=utf-8\n\nEmail body"
        self.assertEqual(extract(Path("sample.eml"), email)[0]["text"], "Email body")

    def test_missing_pdf_dependency_is_explicit(self):
        self.write("sample.pdf", "%PDF-1.0 fake")
        with patch("voicework.discovery.shutil.which", return_value=None):
            result = discover([str(self.root)])
        self.assertFalse(result["capabilities"]["pdf_available"])
        self.assertTrue(any("pdftotext" in error for error in result["errors"]))

    def test_malformed_json_and_binary_do_not_abort_scan(self):
        self.write("bad.json", "{not json")
        self.write("binary.txt", "Hello\x00binary")
        self.write("valid.md", "Still available")
        result = discover([str(self.root)])
        self.assertEqual([i["text"] for i in result["items"]], ["Still available"])
        self.assertEqual(len(result["errors"]), 2)


if __name__ == "__main__":
    unittest.main()
