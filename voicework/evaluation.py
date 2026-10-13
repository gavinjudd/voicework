"""Blinded, same-task comparisons kept separate from calibration feedback.

Only completed Codex comparisons enter the real-model preference counts. These
counts describe this user's ratings, not a writing-accuracy estimate.
"""
import copy
import json
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .learning import retrieve, validate_context
from .store import text_field


NEUTRAL_CONTROLS = {"polish": 50, "detail": 50, "warmth": 50, "assertiveness": 50, "structure": "auto"}
CHOICES = {"a", "b", "tie", "neither"}
REHEARSAL_WARNING = "Offline evaluation is workflow rehearsal and is excluded from real-model preference counts."
EVALUATION_WARNING = "Judge the same brief in both drafts. Labels are hidden until rating; ratings are not an accuracy score."


def _now():
    return datetime.now(timezone.utc).isoformat()


def _validated_output(value, mode):
    if not isinstance(value, dict) or value.get("provider") != mode:
        raise ValueError("Evaluation provider changed or returned an invalid response")
    text = text_field(value.get("text"), "evaluation draft", 30000, True)
    warnings = value.get("warnings", [])
    if not isinstance(warnings, list) or len(warnings) > 20 or any(not isinstance(w, str) or len(w) > 2000 for w in warnings):
        raise ValueError("Invalid evaluation provider warnings")
    return {"text": text, "warnings": warnings}


def _counts():
    return {name: 0 for name in ("total", "unrated", "rated_real", "calibrated_wins", "baseline_wins", "ties", "neither", "factual_flags", "offline_count", "rated_offline")}


class EvaluationStore:
    def __init__(self, sqlite_path):
        self.path = Path(sqlite_path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""CREATE TABLE IF NOT EXISTS eval_cases (
                id TEXT PRIMARY KEY,
                context TEXT NOT NULL, brief TEXT NOT NULL, options TEXT NOT NULL,
                provider TEXT NOT NULL CHECK(provider IN ('offline','codex')),
                model TEXT NOT NULL, controls TEXT NOT NULL,
                mapping TEXT NOT NULL, provider_warnings TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'unrated' CHECK(status IN ('unrated','rated')),
                created_at TEXT NOT NULL, choice TEXT,
                factual_issue INTEGER, notes TEXT, rated_at TEXT
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
    def _public(row):
        # Explicit allowlist: no blind mapping, candidate warnings, or evidence
        # metadata escapes through create/list responses before the vote.
        value = {key: row[key] for key in ("id", "brief", "provider", "status", "created_at")}
        value["context"] = json.loads(row["context"])
        value["options"] = json.loads(row["options"])
        value["warnings"] = [REHEARSAL_WARNING if row["provider"] == "offline" else EVALUATION_WARNING]
        if row["status"] == "rated":
            mapping = json.loads(row["mapping"])
            value.update(mapping=mapping, choice=row["choice"], factual_issue=bool(row["factual_issue"]),
                         notes=row["notes"], rated_at=row["rated_at"],
                         result=mapping.get(row["choice"], row["choice"]))
            for source, warnings in json.loads(row["provider_warnings"]).items():
                value["warnings"].extend("%s: %s" % (source.capitalize(), warning) for warning in warnings)
        return value

    def create_case(self, context, brief, provider, evidence, preferences):
        context = validate_context(context)
        brief = text_field(brief, "brief", 15000, True)
        mode = getattr(provider, "mode", None)
        if mode not in {"offline", "codex"}:
            raise ValueError("Evaluation requires an explicit offline or codex provider")
        if not isinstance(evidence, list) or not all(isinstance(item, dict) for item in evidence):
            raise ValueError("Evaluation evidence must be a list of samples")
        if not isinstance(preferences, list) or not all(isinstance(item, dict) for item in preferences):
            raise ValueError("Evaluation preferences must be a list of feedback records")
        approved = copy.deepcopy(retrieve(evidence, context))
        preferences = copy.deepcopy(preferences)
        model = text_field(getattr(provider, "model", "") or "", "model", 200)
        # No transaction is held while the provider is running. Failed or partial
        # generation cannot leave a case that is eligible for rating.
        baseline = _validated_output(provider.generate_draft(copy.deepcopy(context), brief, dict(NEUTRAL_CONTROLS), [], []), mode)
        calibrated = _validated_output(provider.generate_draft(copy.deepcopy(context), brief, dict(NEUTRAL_CONTROLS), copy.deepcopy(approved), copy.deepcopy(preferences)), mode)
        order = ("calibrated", "baseline") if secrets.choice((False, True)) else ("baseline", "calibrated")
        generated = {"baseline": baseline, "calibrated": calibrated}
        mapping = dict(zip(("a", "b"), order))
        options = [{"id": option_id, "text": generated[source]["text"]} for option_id, source in mapping.items()]
        case_id = uuid.uuid4().hex
        with self._connect() as conn:
            conn.execute("""INSERT INTO eval_cases
                (id,context,brief,options,provider,model,controls,mapping,provider_warnings,status,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,'unrated',?)""",
                (case_id, json.dumps(context), brief, json.dumps(options), mode, model,
                 json.dumps(NEUTRAL_CONTROLS), json.dumps(mapping),
                 json.dumps({key: output["warnings"] for key, output in generated.items()}), _now()))
            return self._public(conn.execute("SELECT * FROM eval_cases WHERE id=?", (case_id,)).fetchone())

    def rate_case(self, case_id, choice, factual_issue=False, notes=""):
        case_id = text_field(case_id, "case_id", 100, True)
        if not isinstance(choice, str) or choice not in CHOICES:
            raise ValueError("choice must be a, b, tie, or neither")
        if not isinstance(factual_issue, bool):
            raise ValueError("factual_issue must be true or false")
        notes = text_field(notes, "notes", 5000)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM eval_cases WHERE id=?", (case_id,)).fetchone()
            if row is None:
                raise KeyError("Evaluation case not found")
            if row["status"] != "unrated":
                raise ValueError("This evaluation has already been rated; ratings are immutable")
            conn.execute("UPDATE eval_cases SET status='rated',choice=?,factual_issue=?,notes=?,rated_at=? WHERE id=? AND status='unrated'",
                         (choice, int(factual_issue), notes, _now(), case_id))
            return self._public(conn.execute("SELECT * FROM eval_cases WHERE id=?", (case_id,)).fetchone())

    def list_cases(self):
        with self._connect() as conn:
            return [self._public(row) for row in conn.execute("SELECT * FROM eval_cases ORDER BY created_at DESC,id DESC")]

    def summary(self):
        summary, contexts = _counts(), {}
        with self._connect() as conn:
            rows = conn.execute("SELECT context,provider,status,choice,mapping,factual_issue FROM eval_cases").fetchall()
        for row in rows:
            channel = json.loads(row["context"])["channel"]
            counts = contexts.setdefault(channel, _counts())
            for target in (summary, counts):
                target["total"] += 1
                if row["status"] == "unrated":
                    target["unrated"] += 1
                if row["provider"] == "offline":
                    target["offline_count"] += 1
                    target["rated_offline"] += row["status"] == "rated"
                elif row["provider"] == "codex" and row["status"] == "rated":
                    target["rated_real"] += 1
                    target["factual_flags"] += bool(row["factual_issue"])
                    result = json.loads(row["mapping"]).get(row["choice"], row["choice"])
                    target[{"calibrated": "calibrated_wins", "baseline": "baseline_wins", "tie": "ties", "neither": "neither"}[result]] += 1
        summary["per_context"] = [{"channel": channel, **counts} for channel, counts in sorted(contexts.items())]
        return summary
