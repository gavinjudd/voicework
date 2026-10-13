"""Explicit style corrections, atomic lesson creation, and bounded safe undo.

Source drafts are audit material only. They never become authored samples or
automatically enter generation evidence.
"""
import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .language import LanguageLessonStore, matching_lessons, validate_lesson
from .learning import CONTEXT_FIELDS, validate_context
from .store import text_field

SNAPSHOT_FIELDS = ("id", "context", "dimension", "guidance", "preferred", "avoided", "created_at")
CONDITION_FIELDS = ("audience", "intent", "relationship", "stakes")


class CorrectionConflict(ValueError):
    """The recorded correction cannot safely mutate the current lesson."""


def lesson_fingerprint(lesson):
    snapshot = {field: lesson[field] for field in SNAPSHOT_FIELDS}
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _same_text(first, second):
    return " ".join(first.casefold().split()) == " ".join(second.casefold().split())


def validate_correction(packet):
    allowed = {"original_text", "revised_text", "guidance", "preferred", "avoided", "context", "scope", "dimension", "kind"}
    if not isinstance(packet, dict) or set(packet) - allowed:
        raise ValueError("Unknown correction field")
    if packet.get("kind") != "style":
        raise ValueError("Only kind='style' can create a language lesson; factual edits must remain local edits")
    original = text_field(packet.get("original_text", ""), "original_text", 30000, True)
    revised = text_field(packet.get("revised_text", ""), "revised_text", 30000, True)
    if original == revised:
        raise ValueError("Change the draft before remembering a correction")
    guidance = text_field(packet.get("guidance", ""), "guidance", 2000, True)
    scope = packet.get("scope")
    if not isinstance(scope, str) or scope not in {"similar", "channel"}:
        raise ValueError("scope must be similar or channel")
    source_context = validate_context(packet.get("context"))
    context = dict(source_context)
    if scope == "similar":
        if not any(context[field] for field in CONDITION_FIELDS):
            raise ValueError("Similar situations requires an audience, intent, relationship, or stakes condition")
    else:
        context = {field: context["channel"] if field == "channel" else "" for field in CONTEXT_FIELDS}
    lesson = validate_lesson({"context": context, "dimension": packet.get("dimension", "diction"), "guidance": guidance,
                              "preferred": packet.get("preferred", ""), "avoided": packet.get("avoided", "")})
    return {**lesson, "original_text": original, "revised_text": revised,
            "source_context": source_context, "scope": scope, "kind": "style"}


class CorrectionStore:
    def __init__(self, sqlite_path):
        self.path = Path(sqlite_path)
        # Uses the existing lesson schema; all per-correction writes below share
        # one connection and transaction so partial saves cannot create a rule.
        LanguageLessonStore(self.path)
        with self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS corrections (
                id TEXT PRIMARY KEY, lesson_id TEXT NOT NULL,
                original_text TEXT NOT NULL, revised_text TEXT NOT NULL,
                guidance TEXT NOT NULL, preferred TEXT NOT NULL, avoided TEXT NOT NULL,
                context TEXT NOT NULL, source_context TEXT NOT NULL,
                scope TEXT NOT NULL, dimension TEXT NOT NULL, kind TEXT NOT NULL,
                lesson_snapshot TEXT NOT NULL, lesson_fingerprint TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL, undone_at TEXT
            )""")

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
    def _public(row, lesson):
        item = dict(row)
        for field in ("context", "source_context", "lesson_snapshot"):
            item[field] = json.loads(item[field])
        if item["status"] != "undone":
            if lesson is None:
                item["status"] = "lesson_deleted"
            elif lesson_fingerprint(lesson) != item["lesson_fingerprint"]:
                item["status"] = "lesson_changed"
            elif not lesson["enabled"]:
                item["status"] = "inactive"
        return item

    @staticmethod
    def _load(conn, ident):
        row = conn.execute("SELECT * FROM corrections WHERE id=?", (ident,)).fetchone()
        if row is None:
            raise KeyError("Correction not found")
        lesson_row = conn.execute("SELECT * FROM language_lessons WHERE id=?", (row["lesson_id"],)).fetchone()
        lesson = LanguageLessonStore._record(lesson_row) if lesson_row is not None else None
        return CorrectionStore._public(row, lesson), lesson

    def add(self, packet):
        value = validate_correction(packet)
        created_at = datetime.now(timezone.utc).isoformat()
        lesson = {key: value[key] for key in ("context", "dimension", "guidance", "preferred", "avoided")}
        lesson.update(id=uuid.uuid4().hex, enabled=True, created_at=created_at)
        item = {**value, "id": uuid.uuid4().hex, "lesson_id": lesson["id"], "lesson_snapshot": lesson,
                "lesson_fingerprint": lesson_fingerprint(lesson), "status": "active", "created_at": created_at, "undone_at": None}
        encoded = dict(item)
        for field in ("context", "source_context", "lesson_snapshot"):
            encoded[field] = json.dumps(item[field], ensure_ascii=False)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""INSERT INTO language_lessons (id,context,dimension,guidance,preferred,avoided,enabled,created_at)
                VALUES (?,?,?,?,?,?,1,?)""", (lesson["id"], json.dumps(lesson["context"]), lesson["dimension"],
                lesson["guidance"], lesson["preferred"], lesson["avoided"], created_at))
            conn.execute("""INSERT INTO corrections
                (id,lesson_id,original_text,revised_text,guidance,preferred,avoided,context,source_context,
                 scope,dimension,kind,lesson_snapshot,lesson_fingerprint,status,created_at,undone_at)
                VALUES (:id,:lesson_id,:original_text,:revised_text,:guidance,:preferred,:avoided,:context,:source_context,
                        :scope,:dimension,:kind,:lesson_snapshot,:lesson_fingerprint,:status,:created_at,:undone_at)""", encoded)
        return {"correction": item, "lesson": lesson}

    def get(self, ident):
        ident = text_field(ident, "correction_id", 100, True)
        with self._connect() as conn:
            return self._load(conn, ident)[0]

    def list(self):
        with self._connect() as conn:
            lessons = {row["id"]: LanguageLessonStore._record(row) for row in conn.execute("SELECT * FROM language_lessons")}
            return [self._public(row, lessons.get(row["lesson_id"])) for row in conn.execute("SELECT * FROM corrections ORDER BY created_at DESC,id DESC")]

    def require_active(self, ident):
        ident = text_field(ident, "correction_id", 100, True)
        with self._connect() as conn:
            correction, lesson = self._load(conn, ident)
        if correction["status"] != "active":
            raise CorrectionConflict("This correction is inactive, changed, or deleted; only its unchanged active lesson can be checked")
        return correction, lesson

    def prepare_check(self, ident, context, brief):
        context = validate_context(context)
        brief = text_field(brief, "brief", 15000, True)
        correction, lesson = self.require_active(ident)
        if not matching_lessons([lesson], context):
            raise ValueError("The new check must match the correction's declared context")
        if any(_same_text(brief, correction[field]) for field in ("original_text", "revised_text")):
            raise ValueError("Use a different brief to check whether the correction generalizes")
        return correction, lesson, context, brief

    def undo(self, ident):
        ident = text_field(ident, "correction_id", 100, True)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            correction, lesson = self._load(conn, ident)
            if correction["status"] == "undone":
                # Do not disable a lesson a user deliberately re-enabled later.
                return {"correction": correction, "lesson": lesson}
            if correction["status"] in {"lesson_changed", "lesson_deleted"}:
                raise CorrectionConflict("The lesson was changed or deleted after this correction. Nothing was changed; review the current lesson directly")
            conn.execute("UPDATE language_lessons SET enabled=0 WHERE id=?", (correction["lesson_id"],))
            conn.execute("UPDATE corrections SET status='undone',undone_at=? WHERE id=?",
                         (datetime.now(timezone.utc).isoformat(), ident))
            updated, lesson = self._load(conn, ident)
            return {"correction": updated, "lesson": lesson}
