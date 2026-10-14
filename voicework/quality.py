"""Blind correction checks against the same current profile, never training data.

Only the selected language lesson differs between variants. Persisted provenance
contains identifiers and hashes, not old source/evidence snapshots to revive.
"""
import copy
import hashlib
import json
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .corrections import SNAPSHOT_FIELDS, lesson_fingerprint
from .evaluation import NEUTRAL_CONTROLS
from .language import matching_lessons, validate_lesson
from .learning import contextual_preferences, retrieve, validate_context
from .matching import same_channel
from .store import text_field
from .migrations import allow_live_provider


VARIANTS = ("previous", "updated")
MEANING = ("preserved", "changed", "unsure")
EFFORT = ("none", "light", "substantial")
WARNING = "Compare voice, meaning and editing effort separately. Labels are hidden until rating; this is not an accuracy score."
REHEARSAL_WARNING = "Offline correction checks are workflow rehearsal and are excluded from real-model quality counts."
LESSON_FIELDS = SNAPSHOT_FIELDS


def _now():
    return datetime.now(timezone.utc).isoformat()


def _fingerprint(value):
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("Quality-check inputs must contain JSON-compatible values") from exc
    return hashlib.sha256(encoded).hexdigest()


def _reference(item, kind):
    return {"id": text_field(item.get("id", ""), "reference id", 100),
            "kind": kind, "fingerprint": _fingerprint(item)}


def _output(value, mode):
    if not isinstance(value, dict) or value.get("provider") != mode:
        raise ValueError("Quality-check provider changed or returned an invalid response")
    text = text_field(value.get("text"), "quality-check draft", 30000, True)
    warnings = value.get("warnings", [])
    if not isinstance(warnings, list) or len(warnings) > 20 or any(not isinstance(w, str) or len(w) > 2000 for w in warnings):
        raise ValueError("Invalid quality-check provider warnings")
    return {"text": text, "warnings": warnings}


def _counts():
    counts = {key: 0 for key in ("total", "unrated", "rated_real", "previous_wins", "updated_wins", "ties", "neither", "meaning_issues", "offline_count", "rated_offline")}
    counts["meaning"] = {variant: {label: 0 for label in MEANING} for variant in VARIANTS}
    counts["effort"] = {variant: {label: 0 for label in EFFORT} for variant in VARIANTS}
    return counts


def _eligible_preferences(preferences, context, target_id):
    result = []
    for item in preferences:
        if item.get("id") == target_id or item.get("held_out") or item.get("enabled") is False:
            continue
        if item.get("is_language_lesson") is True:
            if item.get("id") == target_id or not matching_lessons([item], context):
                continue
        elif item.get("is_prior") is True:
            if item.get("confirmed") is not True:
                continue
            if not same_channel(validate_context(item.get("context")), context):
                continue
        elif not contextual_preferences([item], context):
            continue
        result.append(item)
    return result


class QualityStore:
    def __init__(self, sqlite_path):
        self.path = Path(sqlite_path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""CREATE TABLE IF NOT EXISTS quality_cases (
                id TEXT PRIMARY KEY, correction_id TEXT NOT NULL,
                context TEXT NOT NULL, brief TEXT NOT NULL, options TEXT NOT NULL,
                provider TEXT NOT NULL CHECK(provider IN ('offline','codex','anthropic')),
                model TEXT NOT NULL, controls TEXT NOT NULL, provenance TEXT NOT NULL,
                mapping TEXT NOT NULL, generation_order TEXT NOT NULL,
                provider_warnings TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'unrated' CHECK(status IN ('unrated','rated')),
                created_at TEXT NOT NULL, choice TEXT, meaning TEXT, effort TEXT,
                notes TEXT, rated_at TEXT
            )""")
            allow_live_provider(conn, "quality_cases")
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
    def _assert_current_target(conn, correction_id, lesson_id, fingerprint):
        # Called again inside the insertion transaction: editing/disabling the
        # lesson or undoing the correction during generation invalidates the case.
        correction = conn.execute("SELECT lesson_id,lesson_fingerprint,status FROM corrections WHERE id=?", (correction_id,)).fetchone()
        lesson = conn.execute("SELECT * FROM language_lessons WHERE id=?", (lesson_id,)).fetchone()
        if correction is None or lesson is None:
            raise ValueError("The correction or its language lesson is no longer available")
        if correction["status"] != "active" or correction["lesson_id"] != lesson_id or correction["lesson_fingerprint"] != fingerprint:
            raise ValueError("The correction changed or is no longer active")
        item = dict(lesson)
        item["context"] = json.loads(item["context"])
        if not item["enabled"] or lesson_fingerprint(item) != fingerprint:
            raise ValueError("The correction's language lesson changed or is disabled")

    @staticmethod
    def _public(row):
        # An explicit allowlist prevents state/list responses leaking the blind
        # mapping, generation order, per-candidate warnings or provenance.
        value = {key: row[key] for key in ("id", "correction_id", "brief", "provider", "model", "status", "created_at")}
        value["context"] = json.loads(row["context"])
        value["options"] = json.loads(row["options"])
        value["warnings"] = [WARNING]
        if row["provider"] == "offline":
            value["warnings"].append(REHEARSAL_WARNING)
        if row["status"] == "rated":
            mapping = json.loads(row["mapping"])
            meaning, effort = json.loads(row["meaning"]), json.loads(row["effort"])
            value.update(mapping=mapping, choice=row["choice"], meaning=meaning, effort=effort,
                         notes=row["notes"], rated_at=row["rated_at"], result=mapping.get(row["choice"], row["choice"]),
                         observations={variant: {"meaning": meaning[option], "effort": effort[option]} for option, variant in mapping.items()},
                         controls=json.loads(row["controls"]), provenance=json.loads(row["provenance"]))
            for variant, warnings in json.loads(row["provider_warnings"]).items():
                label = "Without this correction" if variant == "previous" else "With this correction"
                value["warnings"].extend("%s: %s" % (label, warning) for warning in warnings)
        return value

    def create_case(self, correction, context, brief, provider, evidence, common_preferences, target_lesson):
        context = validate_context(context)
        brief = text_field(brief, "brief", 15000, True)
        if not isinstance(correction, dict) or correction.get("status") != "active":
            raise ValueError("Select an active saved correction")
        correction_id = text_field(correction.get("id"), "correction id", 100, True)
        lesson_id = text_field(correction.get("lesson_id"), "lesson id", 100, True)
        fingerprint = text_field(correction.get("lesson_fingerprint"), "lesson fingerprint", 64, True)
        if not isinstance(target_lesson, dict) or target_lesson.get("id") != lesson_id or target_lesson.get("enabled") is not True:
            raise ValueError("The selected correction requires its enabled language lesson")
        target = copy.deepcopy(target_lesson)
        validate_lesson({key: target.get(key) for key in ("context", "dimension", "guidance", "preferred", "avoided")})
        if any(key not in target for key in LESSON_FIELDS) or lesson_fingerprint(target) != fingerprint:
            raise ValueError("The selected correction's language lesson has changed")
        if not matching_lessons([target], context):
            raise ValueError("The correction does not apply to this context")
        # Keep only the lesson's defined fields, never a correction's source text.
        target = {**{key: target[key] for key in LESSON_FIELDS}, "enabled": True, "is_language_lesson": True}
        mode = getattr(provider, "mode", None)
        if mode not in {"offline", "codex", "anthropic"}:
            raise ValueError("Quality checks require an explicit offline, codex or anthropic provider")
        model = text_field(getattr(provider, "model", "") or "", "model", 200)
        for label, records in (("evidence", evidence), ("common preferences", common_preferences)):
            if not isinstance(records, list) or not all(isinstance(item, dict) for item in records):
                raise ValueError("Quality-check %s must be a list of records" % label)
        approved = copy.deepcopy(retrieve(evidence, context))
        common = copy.deepcopy(_eligible_preferences(common_preferences, context, lesson_id))
        controls = copy.deepcopy(NEUTRAL_CONTROLS)
        provenance = {
            "correction_id": correction_id, "lesson_id": lesson_id, "lesson_fingerprint": fingerprint,
            "evidence": [_reference(item, "sample") for item in approved],
            "common_preferences": [_reference(item, "language_lesson" if item.get("is_language_lesson") else "prior" if item.get("is_prior") else "feedback") for item in common],
            "context_fingerprint": _fingerprint(context), "brief_fingerprint": _fingerprint(brief),
            "common_inputs_fingerprint": _fingerprint({"context": context, "brief": brief, "controls": controls,
                                                       "evidence": approved, "preferences": common, "provider": mode, "model": model}),
            "provider": mode, "model": model, "controls": copy.deepcopy(controls),
        }
        with self._connect() as conn:
            self._assert_current_target(conn, correction_id, lesson_id, fingerprint)
        generation_order = ("updated", "previous") if secrets.choice((False, True)) else VARIANTS
        generated = {}
        for variant in generation_order:
            if getattr(provider, "mode", None) != mode or (getattr(provider, "model", "") or "") != model:
                raise ValueError("Quality-check provider or model changed during generation")
            preferences = common + ([target] if variant == "updated" else [])
            generated[variant] = _output(provider.generate_draft(copy.deepcopy(context), brief, copy.deepcopy(controls),
                                                                copy.deepcopy(approved), copy.deepcopy(preferences)), mode)
            if getattr(provider, "mode", None) != mode or (getattr(provider, "model", "") or "") != model:
                raise ValueError("Quality-check provider or model changed during generation")
        order = ("updated", "previous") if secrets.choice((False, True)) else VARIANTS
        mapping = dict(zip(("a", "b"), order))
        options = [{"id": option, "text": generated[variant]["text"]} for option, variant in mapping.items()]
        case_id = uuid.uuid4().hex
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_current_target(conn, correction_id, lesson_id, fingerprint)
            conn.execute("""INSERT INTO quality_cases
                (id,correction_id,context,brief,options,provider,model,controls,provenance,mapping,generation_order,provider_warnings,status,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'unrated',?)""",
                (case_id, correction_id, json.dumps(context), brief, json.dumps(options), mode, model, json.dumps(controls),
                 json.dumps(provenance), json.dumps(mapping), json.dumps(generation_order),
                 json.dumps({variant: generated[variant]["warnings"] for variant in VARIANTS}), _now()))
            return self._public(conn.execute("SELECT * FROM quality_cases WHERE id=?", (case_id,)).fetchone())

    def rate_case(self, case_id, payload):
        case_id = text_field(case_id, "case id", 100, True)
        if not isinstance(payload, dict) or set(payload) - {"choice", "meaning", "effort", "notes"}:
            raise ValueError("Rating accepts choice, meaning, effort and optional notes")
        choice = payload.get("choice")
        if not isinstance(choice, str) or choice not in {"a", "b", "tie", "neither"}:
            raise ValueError("choice must be a, b, tie, or neither")
        for field, labels in (("meaning", MEANING), ("effort", EFFORT)):
            values = payload.get(field)
            if not isinstance(values, dict) or set(values) != {"a", "b"} or any(not isinstance(value, str) or value not in labels for value in values.values()):
                raise ValueError("Rate %s independently for both a and b using %s" % (field, ", ".join(labels)))
        notes = text_field(payload.get("notes", ""), "notes", 5000)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM quality_cases WHERE id=?", (case_id,)).fetchone()
            if row is None:
                raise KeyError("Quality-check case not found")
            if row["status"] != "unrated":
                raise ValueError("This quality check has already been rated; ratings are immutable")
            conn.execute("UPDATE quality_cases SET status='rated',choice=?,meaning=?,effort=?,notes=?,rated_at=? WHERE id=? AND status='unrated'",
                         (choice, json.dumps(payload["meaning"]), json.dumps(payload["effort"]), notes, _now(), case_id))
            return self._public(conn.execute("SELECT * FROM quality_cases WHERE id=?", (case_id,)).fetchone())

    def list_cases(self):
        with self._connect() as conn:
            return [self._public(row) for row in conn.execute("SELECT * FROM quality_cases ORDER BY created_at DESC,id DESC")]

    def summary(self):
        summary, corrections = _counts(), {}
        with self._connect() as conn:
            rows = conn.execute("SELECT correction_id,provider,status,choice,mapping,meaning,effort FROM quality_cases").fetchall()
        for row in rows:
            counts = corrections.setdefault(row["correction_id"], _counts())
            for target in (summary, counts):
                target["total"] += 1
                if row["status"] == "unrated":
                    target["unrated"] += 1
                if row["provider"] == "offline":
                    target["offline_count"] += 1
                    target["rated_offline"] += row["status"] == "rated"
                elif row["provider"] in ("codex", "anthropic") and row["status"] == "rated":
                    mapping = json.loads(row["mapping"])
                    meaning, effort = json.loads(row["meaning"]), json.loads(row["effort"])
                    result = mapping.get(row["choice"], row["choice"])
                    target["rated_real"] += 1
                    target[{"previous": "previous_wins", "updated": "updated_wins", "tie": "ties", "neither": "neither"}[result]] += 1
                    target["meaning_issues"] += "changed" in meaning.values()
                    for option, variant in mapping.items():
                        target["meaning"][variant][meaning[option]] += 1
                        target["effort"][variant][effort[option]] += 1
        summary["per_correction"] = [{"correction_id": ident, **counts} for ident, counts in sorted(corrections.items())]
        return summary
