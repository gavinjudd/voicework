"""Lesson proposals from connected assistants, held for explicit user review.

An assistant (for example through the MCP server) may suggest a language
lesson. A proposal never affects drafting: it is a separate pending record
until the user accepts it in Voicework, which creates an ordinary enabled
lesson through the normal validation path, or dismisses it. Before/after text
is kept only as local provenance and never becomes an authored sample.
"""
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .language import validate_lesson
from .store import text_field

MAX_PENDING = 50
STATUSES = ("pending", "accepted", "dismissed")


def validate_proposal(packet):
    if not isinstance(packet, dict):
        raise ValueError("A proposal must be an object")
    allowed = {"context", "dimension", "guidance", "preferred", "avoided", "rationale", "before", "after", "source"}
    if set(packet) - allowed:
        raise ValueError("Unknown proposal field")
    lesson = validate_lesson({key: packet[key] for key in ("context", "dimension", "guidance", "preferred", "avoided") if key in packet})
    extra = {"rationale": text_field(packet.get("rationale", ""), "rationale", 1000),
             "before": text_field(packet.get("before", ""), "before", 4000),
             "after": text_field(packet.get("after", ""), "after", 4000),
             "source": text_field(packet.get("source", "assistant"), "source", 120) or "assistant"}
    return lesson, extra


class ProposalStore:
    def __init__(self, sqlite_path):
        self.path = Path(sqlite_path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS lesson_proposals (
                id TEXT PRIMARY KEY, lesson TEXT NOT NULL, rationale TEXT NOT NULL,
                before_text TEXT NOT NULL, after_text TEXT NOT NULL, source TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','accepted','dismissed')),
                created_at TEXT NOT NULL, decided_at TEXT, lesson_id TEXT
            )""")
        self.path.chmod(0o600)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self.path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA secure_delete=ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def _record(row):
        item = dict(row)
        item["lesson"] = json.loads(item["lesson"])
        item["before"], item["after"] = item.pop("before_text"), item.pop("after_text")
        return item

    def add(self, packet):
        lesson, extra = validate_proposal(packet)
        item = {"id": uuid.uuid4().hex, "lesson": lesson, "status": "pending",
                "created_at": datetime.now(timezone.utc).isoformat(), "decided_at": None, "lesson_id": None, **extra}
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            pending = conn.execute("SELECT COUNT(*) FROM lesson_proposals WHERE status='pending'").fetchone()[0]
            if pending >= MAX_PENDING:
                raise ValueError("%d proposals are already waiting for review in Voicework" % MAX_PENDING)
            conn.execute("INSERT INTO lesson_proposals VALUES (?,?,?,?,?,?,?,?,?,?)", (
                item["id"], json.dumps(lesson), extra["rationale"], extra["before"], extra["after"], extra["source"],
                "pending", item["created_at"], None, None))
        return item

    def list(self, status=None):
        if status is not None and status not in STATUSES:
            raise ValueError("Unknown proposal status")
        with self._connect() as conn:
            query = "SELECT * FROM lesson_proposals" + (" WHERE status=?" if status else "") + " ORDER BY created_at DESC,id DESC"
            return [self._record(row) for row in conn.execute(query, (status,) if status else ())]

    def decide(self, ident, decision, lesson_store=None):
        """Accept (creating a lesson via the normal store) or dismiss a pending proposal."""
        if decision not in ("accept", "dismiss"):
            raise ValueError("Decision must be accept or dismiss")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM lesson_proposals WHERE id=?", (ident,)).fetchone()
            if row is None:
                raise KeyError("Proposal not found")
            if row["status"] != "pending":
                raise ValueError("This proposal was already %s" % row["status"])
            lesson_id = None
            if decision == "accept":
                if lesson_store is None:
                    raise ValueError("Accepting requires the lesson store")
                # Same validation as a directly taught lesson, written in this
                # transaction so a proposal is never accepted twice.
                lesson = validate_lesson(json.loads(row["lesson"]))
                lesson_id = uuid.uuid4().hex
                conn.execute("INSERT INTO language_lessons (id,context,dimension,guidance,preferred,avoided,enabled,created_at) VALUES (?,?,?,?,?,?,?,?)", (
                    lesson_id, json.dumps(lesson["context"]), lesson["dimension"], lesson["guidance"], lesson["preferred"],
                    lesson["avoided"], 1, datetime.now(timezone.utc).isoformat()))
            conn.execute("UPDATE lesson_proposals SET status=?, decided_at=?, lesson_id=? WHERE id=?", (
                "accepted" if decision == "accept" else "dismissed", datetime.now(timezone.utc).isoformat(), lesson_id, ident))
            row = conn.execute("SELECT * FROM lesson_proposals WHERE id=?", (ident,)).fetchone()
        return self._record(row)
