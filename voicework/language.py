"""Direct, contextual language preferences; never authored sample evidence."""
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .learning import validate_context
from .store import text_field

DIMENSIONS = {"vernacular", "syntax", "diction", "rhythm"}
FIELDS = {"context", "dimension", "guidance", "preferred", "avoided"}


def validate_focus(value):
    if not isinstance(value, str) or value not in DIMENSIONS | {"mixed"}:
        raise ValueError("focus must be mixed, vernacular, syntax, diction, or rhythm")
    return value


def validate_lesson(packet):
    if not isinstance(packet, dict) or set(packet) - FIELDS:
        raise ValueError("A language lesson accepts only context, dimension, guidance, preferred, and avoided")
    dimension = packet.get("dimension")
    if not isinstance(dimension, str) or dimension not in DIMENSIONS:
        raise ValueError("dimension must be vernacular, syntax, diction, or rhythm")
    value = {"context": validate_context(packet.get("context")), "dimension": dimension}
    for field, limit in (("guidance", 2000), ("preferred", 500), ("avoided", 500)):
        value[field] = text_field(packet.get(field, ""), field, limit)
    if not any(value[field] for field in ("guidance", "preferred", "avoided")):
        raise ValueError("Add guidance, preferred wording, or wording to avoid")
    return value


def matching_lessons(lessons, context):
    """Match declared conditions exactly; a scenario remains descriptive guidance."""
    requested = validate_context(context)
    result = []
    for lesson in lessons:
        if lesson.get("enabled") is not True:
            continue
        declared = lesson.get("context", {})
        if declared.get("channel", "work").strip().casefold() != requested["channel"].strip().casefold():
            continue
        if any(declared.get(field, "").strip() and declared[field].strip().casefold() != requested[field].strip().casefold()
               for field in ("audience", "intent", "relationship", "stakes")):
            continue
        result.append(lesson)
    return result


class LanguageLessonStore:
    def __init__(self, sqlite_path):
        self.path = Path(sqlite_path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS language_lessons (
                id TEXT PRIMARY KEY, context TEXT NOT NULL, dimension TEXT NOT NULL,
                guidance TEXT NOT NULL, preferred TEXT NOT NULL, avoided TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
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
        item["context"] = json.loads(item["context"])
        item["enabled"] = bool(item["enabled"])
        return item

    def add(self, packet):
        item = validate_lesson(packet)
        item.update(id=uuid.uuid4().hex, enabled=True, created_at=datetime.now(timezone.utc).isoformat())
        with self._connect() as conn:
            conn.execute("INSERT INTO language_lessons VALUES (?,?,?,?,?,?,?,?)", (
                item["id"], json.dumps(item["context"]), item["dimension"], item["guidance"],
                item["preferred"], item["avoided"], 1, item["created_at"]))
        return item

    def list(self):
        with self._connect() as conn:
            return [self._record(row) for row in conn.execute("SELECT * FROM language_lessons ORDER BY created_at DESC,id DESC")]

    def matching(self, context):
        return matching_lessons(self.list(), context)

    def update(self, ident, patch):
        if not isinstance(patch, dict) or not patch or set(patch) - (FIELDS | {"enabled"}):
            raise ValueError("Patch accepts language lesson content, context, dimension, and enabled")
        if "enabled" in patch and not isinstance(patch["enabled"], bool):
            raise ValueError("enabled must be true or false")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM language_lessons WHERE id=?", (ident,)).fetchone()
            if row is None:
                raise KeyError("Language lesson not found")
            item = self._record(row)
            content = {field: patch.get(field, item[field]) for field in FIELDS}
            item.update(validate_lesson(content))
            item["enabled"] = patch.get("enabled", item["enabled"])
            conn.execute("UPDATE language_lessons SET context=?,dimension=?,guidance=?,preferred=?,avoided=?,enabled=? WHERE id=?", (
                json.dumps(item["context"]), item["dimension"], item["guidance"], item["preferred"],
                item["avoided"], int(item["enabled"]), ident))
        return item

    def delete(self, ident):
        with self._connect() as conn:
            if not conn.execute("DELETE FROM language_lessons WHERE id=?", (ident,)).rowcount:
                raise KeyError("Language lesson not found")
