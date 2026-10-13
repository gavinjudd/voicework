"""Small transactional SQLite store; connections are never shared by threads."""
import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .learning import validate_context, validate_presentation

MAX_SAMPLE_CHARS = 30000
STATUSES = {"pending", "confirmed", "assisted", "rejected"}


def _now():
    return datetime.now(timezone.utc).isoformat()


def text_field(value, name, maximum, required=False):
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError("%s must be text of at most %d characters" % (name, maximum))
    value = value.strip()
    if required and not value:
        raise ValueError("%s is required" % name)
    if "\x00" in value:
        raise ValueError("%s cannot contain NUL characters" % name)
    return value


def validate_sample(item):
    if not isinstance(item, dict):
        raise ValueError("Each sample must be an object")
    allowed = {"title", "text", "source_type", "source_path", "source_url", "context"}
    if set(item) - allowed:
        raise ValueError("Unknown sample field; authorship must be reviewed separately")
    result = {
        "title": text_field(item.get("title", "Untitled sample"), "title", 300, True),
        "text": text_field(item.get("text", ""), "text", MAX_SAMPLE_CHARS, True),
        "source_type": text_field(item.get("source_type", "manual"), "source_type", 80, True),
        "source_path": text_field(item.get("source_path", ""), "source_path", 4096),
        "source_url": text_field(item.get("source_url", ""), "source_url", 4096),
        "context": validate_context(item.get("context")),
    }
    if result["source_url"]:
        try:
            url = urlsplit(result["source_url"])
            valid = url.scheme in ("http", "https") and bool(url.hostname) and not url.username and not url.password
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("source_url must be an http(s) URL without credentials")
    result["fingerprint"] = hashlib.sha256(" ".join(result["text"].split()).encode("utf-8")).hexdigest()
    return result


class Store:
    def __init__(self, path):
        self.path = Path(path)
        directory_existed = self.path.parent.exists()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not directory_existed:
            self.path.parent.chmod(0o700)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS samples (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, text TEXT NOT NULL,
                    source_type TEXT NOT NULL, source_path TEXT NOT NULL, source_url TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending', context TEXT NOT NULL,
                    held_out INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
                    fingerprint TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS rounds (
                    id TEXT PRIMARY KEY, context TEXT NOT NULL, options TEXT NOT NULL,
                    provider TEXT NOT NULL, warnings TEXT NOT NULL, evidence_ids TEXT NOT NULL,
                    status TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS feedback (
                    id TEXT PRIMARY KEY, round_id TEXT NOT NULL UNIQUE REFERENCES rounds(id) ON DELETE CASCADE,
                    choice TEXT NOT NULL, edited_text TEXT NOT NULL, notes TEXT NOT NULL,
                    presentation TEXT NOT NULL, created_at TEXT NOT NULL
                );
            """)
        self.path.chmod(0o600)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self.path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA secure_delete=ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def _sample(row):
        value = dict(row)
        value["context"] = json.loads(value["context"])
        value["held_out"] = bool(value["held_out"])
        return value

    @staticmethod
    def _round(row):
        value = dict(row)
        for field in ("context", "options", "warnings", "evidence_ids"):
            value[field] = json.loads(value[field])
        return value

    def samples(self):
        with self._connect() as conn:
            return [self._sample(row) for row in conn.execute("SELECT * FROM samples ORDER BY created_at DESC, id DESC")]

    def add_samples(self, items):
        if not isinstance(items, list) or not 1 <= len(items) <= 500:
            raise ValueError("items must contain 1 to 500 samples")
        # Validate the entire batch first: a malformed import never partially writes.
        validated = [validate_sample(item) for item in items]
        added, duplicates, samples = 0, 0, []
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for item in validated:
                existing = conn.execute("SELECT * FROM samples WHERE fingerprint=?", (item["fingerprint"],)).fetchone()
                if existing is not None:
                    samples.append(self._sample(existing))
                    duplicates += 1
                    continue
                sample_id = uuid.uuid4().hex
                conn.execute("""INSERT INTO samples
                    (id,title,text,source_type,source_path,source_url,status,context,held_out,created_at,fingerprint)
                    VALUES (?,?,?,?,?,?,'pending',?,0,?,?)""",
                    (sample_id, item["title"], item["text"], item["source_type"], item["source_path"],
                     item["source_url"], json.dumps(item["context"]), _now(), item["fingerprint"]))
                samples.append(self._sample(conn.execute("SELECT * FROM samples WHERE id=?", (sample_id,)).fetchone()))
                added += 1
        return {"added": added, "duplicates": duplicates, "samples": samples}

    def add_sample(self, item):
        result = self.add_samples([item])
        return result["samples"][0]

    def update_sample(self, sample_id, patch):
        if not isinstance(patch, dict) or not patch or set(patch) - {"status", "context", "title", "held_out"}:
            raise ValueError("Patch may include status, context, title, or held_out")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM samples WHERE id=?", (sample_id,)).fetchone()
            if row is None:
                raise KeyError("Sample not found")
            value = self._sample(row)
            if "status" in patch:
                if not isinstance(patch["status"], str) or patch["status"] not in STATUSES:
                    raise ValueError("Unknown sample status")
                value["status"] = patch["status"]
            if "context" in patch:
                value["context"] = validate_context(patch["context"])
            if "title" in patch:
                value["title"] = text_field(patch["title"], "title", 300, True)
            if "held_out" in patch:
                if not isinstance(patch["held_out"], bool):
                    raise ValueError("held_out must be true or false")
                value["held_out"] = patch["held_out"]
            conn.execute("UPDATE samples SET status=?,context=?,title=?,held_out=? WHERE id=?",
                         (value["status"], json.dumps(value["context"]), value["title"], int(value["held_out"]), sample_id))
            return value

    def delete_sample(self, sample_id):
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute("DELETE FROM samples WHERE id=?", (sample_id,))
            if cursor.rowcount == 0:
                raise KeyError("Sample not found")
            for row in conn.execute("SELECT id,evidence_ids FROM rounds").fetchall():
                ids = json.loads(row["evidence_ids"])
                if sample_id in ids:
                    conn.execute("UPDATE rounds SET evidence_ids=? WHERE id=?", (json.dumps([i for i in ids if i != sample_id]), row["id"]))

    def rounds(self):
        with self._connect() as conn:
            return [self._round(row) for row in conn.execute("SELECT * FROM rounds ORDER BY created_at DESC, id DESC")]

    def add_round(self, context, generated, evidence_ids):
        context = validate_context(context)
        options = generated.get("options")
        if not isinstance(options, list) or len(options) != 2:
            raise ValueError("Provider must return two options")
        clean_options = []
        for index, option in enumerate(options):
            if not isinstance(option, dict) or option.get("id") != ("a", "b")[index]:
                raise ValueError("Provider options must be ordered a and b")
            clean_options.append({"id": option["id"], "text": text_field(option.get("text"), "option text", MAX_SAMPLE_CHARS, True)})
        provider = text_field(generated.get("provider"), "provider", 100, True)
        warnings = generated.get("warnings", [])
        if not isinstance(warnings, list) or len(warnings) > 20 or not all(isinstance(w, str) and len(w) <= 2000 for w in warnings):
            raise ValueError("Invalid provider warnings")
        item = {"id": uuid.uuid4().hex, "context": context, "options": clean_options, "provider": provider,
                "warnings": warnings, "evidence_ids": list(evidence_ids), "status": "open", "created_at": _now()}
        with self._connect() as conn:
            conn.execute("INSERT INTO rounds VALUES (?,?,?,?,?,?,?,?)", (
                item["id"], json.dumps(context), json.dumps(clean_options), provider, json.dumps(warnings),
                json.dumps(evidence_ids), item["status"], item["created_at"]))
        return item

    def feedback(self):
        with self._connect() as conn:
            result = []
            rows = conn.execute("""SELECT f.*,r.context,r.options,r.provider FROM feedback f
                JOIN rounds r ON r.id=f.round_id ORDER BY f.created_at DESC,f.id DESC""")
            for row in rows:
                item = dict(row)
                for field in ("context", "presentation", "options"):
                    item[field] = json.loads(item[field])
                item["chosen_text"] = next((o["text"] for o in item["options"] if o["id"] == item["choice"]), "")
                item["rejected_texts"] = [o["text"] for o in item["options"] if o["id"] != item["choice"]]
                result.append(item)
            return result

    def add_feedback(self, payload):
        if not isinstance(payload, dict) or set(payload) - {"round_id", "choice", "edited_text", "notes", "presentation"}:
            raise ValueError("Unknown feedback field")
        round_id = text_field(payload.get("round_id", ""), "round_id", 100, True)
        choice = payload.get("choice")
        if not isinstance(choice, str) or choice not in {"a", "b", "neither"}:
            raise ValueError("choice must be a, b, or neither")
        edited_text = text_field(payload.get("edited_text", ""), "edited_text", MAX_SAMPLE_CHARS)
        notes = text_field(payload.get("notes", ""), "notes", 5000)
        presentation = validate_presentation(payload.get("presentation"))
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT id FROM rounds WHERE id=?", (round_id,)).fetchone() is None:
                raise KeyError("Round not found")
            if conn.execute("SELECT id FROM feedback WHERE round_id=?", (round_id,)).fetchone() is not None:
                raise ValueError("Feedback has already been recorded for this round")
            item_id = uuid.uuid4().hex
            try:
                conn.execute("INSERT INTO feedback VALUES (?,?,?,?,?,?,?)", (
                    item_id, round_id, choice, edited_text, notes, json.dumps(presentation), _now()))
            except sqlite3.IntegrityError as exc:
                raise ValueError("Feedback has already been recorded for this round") from exc
            conn.execute("UPDATE rounds SET status='completed' WHERE id=?", (round_id,))
        return next(item for item in self.feedback() if item["id"] == item_id)
