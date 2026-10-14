"""Provisional context reconstruction and explicitly reviewed prior knowledge.

Neither an analysis result nor a confirmed prior is an authored writing example.
This module never edits a sample or automatically confirms authorship.
"""
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .learning import CONTEXT_FIELDS, validate_context
from .store import text_field

AUTHORSHIP_NOTICE = "Authorship is unverified. Applying context does not confirm that you wrote this."


def analysis_input(sample):
    if not isinstance(sample, dict):
        raise ValueError("Select one sample to analyze")
    text = text_field(sample.get("text", ""), "sample text", 30000, True)
    title = text_field(sample.get("title", "Untitled sample"), "title", 300, True)
    source_type = text_field(sample.get("source_type", "manual"), "source_type", 80)
    source_url = text_field(sample.get("source_url", ""), "source_url", 4096)
    source_path = text_field(sample.get("source_path", ""), "source_path", 4096)
    return {"title": title, "text": text, "source_type": source_type, "source_url": source_url,
            "source_filename": Path(source_path).name if source_path else ""}


def validate_analysis(result, mode):
    expected = {"context", "rationale", "uncertainties", "authorship_signals"}
    if not isinstance(result, dict) or set(result) != expected:
        raise ValueError("Context analysis returned an invalid result")
    if not isinstance(result["context"], dict) or set(result["context"]) != set(CONTEXT_FIELDS):
        raise ValueError("Context analysis must include all six context fields")
    context = validate_context(result["context"])
    output = {"context": context}
    for field in ("rationale", "uncertainties", "authorship_signals"):
        values = result[field]
        if not isinstance(values, list) or not 1 <= len(values) <= 12:
            raise ValueError("Context analysis must include bounded explanation lists")
        output[field] = [text_field(value, field, 1000, True) for value in values]
    if AUTHORSHIP_NOTICE not in output["authorship_signals"]:
        output["authorship_signals"] = [AUTHORSHIP_NOTICE] + output["authorship_signals"][:11]
    output["provider"] = mode
    return output


def infer_context(sample, provider):
    """Analyze only the user-selected sample; return a preview with no writes."""
    if getattr(provider, "mode", None) == "offline":
        return _infer_offline(analysis_input(sample))
    if getattr(provider, "mode", None) not in ("codex", "anthropic"):
        raise ValueError("Unknown context analysis provider")
    return provider.infer_context(sample)


def _infer_offline(sample):
    text, title = sample["text"], sample["title"]
    content = (title + "\n" + text).lower()
    source_type = sample["source_type"].lower()
    context = {field: "unknown" for field in CONTEXT_FIELDS}
    context["scenario"] = "Purpose and audience need review."
    rationale = ["Offline analysis uses metadata and simple text indicators only; every suggestion is provisional."]
    uncertainties = ["The actual sender, audience, relationship and situation cannot be verified from this sample alone."]
    signals = [AUTHORSHIP_NOTICE]

    work = re.findall(r"\b(?:client|colleague|teammate|stakeholder|project|deadline|invoice|proposal|deliverable|meeting|manager|hiring|interview)\b", content)
    personal = re.findall(r"\b(?:love you|miss you|mom|dad|sister|brother|boyfriend|girlfriend|date night|birthday|hang out|weekend plans)\b", content)
    if work and not personal:
        context["channel"] = "work"
        rationale.append("Work-related terms appear in the title or message: " + ", ".join(sorted(set(work))[:4]) + ".")
    elif personal and not work:
        context["channel"] = "personal"
        rationale.append("Personal or family language appears in the title or message.")
    elif work and personal:
        uncertainties.append("Both professional and personal indicators appear; choose the channel yourself.")
    else:
        uncertainties.append("No reliable channel indicator was found; channel remains unknown.")

    greeting = re.search(r"(?im)^\s*(?:hi|hello|hey|dear)\s+([^\n,!:.]{1,60})[,!:.]?\s*$", text)
    if greeting:
        audience = greeting.group(1).strip()
        context["audience"] = audience
        rationale.append("A greeting names a possible recipient; it may belong to quoted material.")
    elif re.search(r"\b(?:team|everyone|all)\b", text[:100], re.I):
        context["audience"] = "group or team (provisional)"
        rationale.append("The opening appears to address a group.")

    if re.search(r"\b(?:mom|dad|sister|brother|parents)\b", content):
        context["relationship"] = "family (provisional)"
    elif re.search(r"\b(?:client|customer)\b", content):
        context["relationship"] = "client/customer (provisional)"
    elif re.search(r"\b(?:teammate|colleague|team)\b", content) and context["channel"] == "work":
        context["relationship"] = "coworker or team (provisional)"

    intents = (
        (r"\b(?:sorry|apologi[sz]e|my fault)\b", "apologize", "An apology or acknowledgment of fault"),
        (r"\b(?:following up|follow up|checking in)\b", "follow up", "A follow-up or check-in"),
        (r"\b(?:could you|can you|please|would you)\b", "make a request", "A request for action or information"),
        (r"\b(?:thank you|thanks|appreciate)\b", "express appreciation", "An expression of appreciation"),
        (r"\b(?:invite|join us|come over|available|schedule)\b", "coordinate plans", "Scheduling or coordinating plans"),
        (r"\b(?:update|completed|finished|progress|status)\b", "share an update", "A progress or status update"),
    )
    for pattern, intent, scenario in intents:
        if re.search(pattern, content):
            context["intent"] = intent
            context["scenario"] = scenario + "; confirm the exact situation before applying."
            rationale.append("The message contains wording consistent with the suggested intent.")
            break
    if re.search(r"\b(?:urgent|asap|legal|termination|resign|final notice|deadline)\b", content):
        context["stakes"] = "potentially elevated; review"
        uncertainties.append("Urgency or consequence-related words appear, but actual stakes are unknown.")
    else:
        uncertainties.append("Stakes are unknown; casual wording does not establish low stakes.")

    if source_type in {"eml", "email"} or greeting or re.search(r"(?im)^\s*(?:best|regards|sincerely|cheers)[,!]?\s*$", text):
        rationale.append("Email-style metadata, greeting or sign-off is present; this does not identify the author.")
    if sample["source_url"]:
        rationale.append("A browser source URL is recorded. Page provenance does not verify who wrote the selected text.")
    if re.search(r"(?im)^\s*(?:>|from:|on .+ wrote:|---+\s*forwarded)", text):
        signals.append("Quoted or forwarded material may include other people's writing; review the boundaries.")
    if re.search(r"\b(?:as an ai|here(?:'s| is) (?:a|the) (?:draft|revised)|assistant:)\b", content):
        signals.append("Assistant-like wording or transcript markers appear; check for AI-assisted or quoted content.")
    if re.search(r"\b(?:I|my|we|our)\b", text):
        signals.append("First-person wording is present, but it does not verify authorship.")
    return validate_analysis({"context": context, "rationale": rationale, "uncertainties": uncertainties, "authorship_signals": signals}, "offline")


def validate_prior(packet):
    if not isinstance(packet, dict) or set(packet) - {"label", "text", "context", "source"}:
        raise ValueError("A prior must contain only label, text, context and source; raw archives are not accepted")
    text = text_field(packet.get("text", ""), "prior text", 4000, True)
    # Structured exports belong in the sample review importer, not this compact
    # instruction/context lane. Never silently flatten a transcript into a prior.
    if text.startswith(("{", "[")):
        try:
            raw = json.loads(text)
        except ValueError:
            raw = None
        if isinstance(raw, (dict, list)):
            raise ValueError("Summarize prior knowledge as compact prose; raw JSON archives are not accepted")
    return {
        "label": text_field(packet.get("label", ""), "prior label", 200, True),
        "text": text,
        "context": validate_context(packet.get("context")),
        "source": text_field(packet.get("source", "User-entered context"), "prior source", 500, True),
    }


class PriorStore:
    """Independent table in the app database; no authored sample writes."""
    def __init__(self, sqlite_path):
        self.path = Path(sqlite_path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS priors (
                id TEXT PRIMARY KEY, label TEXT NOT NULL, text TEXT NOT NULL,
                context TEXT NOT NULL, source TEXT NOT NULL,
                confirmed INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
            )""")
        self.path.chmod(0o600)

    def _connect(self):
        conn = sqlite3.connect(str(self.path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA secure_delete=ON")
        return _ClosingConnection(conn)

    @staticmethod
    def _record(row):
        result = dict(row)
        result["context"] = json.loads(result["context"])
        result["confirmed"] = bool(result["confirmed"])
        return result

    def add(self, packet):
        item = validate_prior(packet)
        item.update(id=uuid.uuid4().hex, confirmed=False, created_at=datetime.now(timezone.utc).isoformat())
        with self._connect() as conn:
            conn.execute("INSERT INTO priors VALUES (?,?,?,?,?,?,?)", (
                item["id"], item["label"], item["text"], json.dumps(item["context"]), item["source"], 0, item["created_at"]))
        return item

    def list(self, confirmed_only=False):
        if not isinstance(confirmed_only, bool):
            raise ValueError("confirmed_only must be true or false")
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM priors" + (" WHERE confirmed=1" if confirmed_only else "") + " ORDER BY created_at DESC,id DESC")
            return [self._record(row) for row in rows]

    def confirm(self, prior_id, confirmed=True):
        if not isinstance(confirmed, bool):
            raise ValueError("confirmed must be true or false")
        with self._connect() as conn:
            cursor = conn.execute("UPDATE priors SET confirmed=? WHERE id=?", (int(confirmed), prior_id))
            if not cursor.rowcount:
                raise KeyError("Prior not found")
            return self._record(conn.execute("SELECT * FROM priors WHERE id=?", (prior_id,)).fetchone())

    def delete(self, prior_id):
        with self._connect() as conn:
            if not conn.execute("DELETE FROM priors WHERE id=?", (prior_id,)).rowcount:
                raise KeyError("Prior not found")


class _ClosingConnection:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        self.connection.__enter__()
        return self.connection

    def __exit__(self, *args):
        try:
            return self.connection.__exit__(*args)
        finally:
            self.connection.close()
