"""Contextual preference estimates with reviewable, retractable evidence.

Strength is the signed weighted mean of reviewed observations. Confidence is
an evidence-support INDEX, not a calibrated probability or voice accuracy:
coverage = independent_mass / (independent_mass + 3), multiplied by consistency
(1 minus the weighted standard deviation of observations on [-1, 1]). A source
batch contributes at most its strongest observation's reliability. Repeated
passages therefore cannot manufacture independent confirmation. All calculations
are replayed from evidence; no model parameters or authored samples are changed.
"""
import hashlib
import json
import math
import sqlite3
import uuid
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .language import DIMENSIONS
from .learning import validate_context
from .matching import lesson_context_matches
from .store import text_field

SOURCE_WEIGHTS = {"direct": 1.0, "edit": 1.0, "correction": 1.0,
                  "reference": .35, "quality": .9, "evaluation": .25, "practice": .5}
METHOD = "grouped-evidence-v1"
NOTICE = ("Confidence describes the amount and consistency of reviewed evidence under a transparent heuristic. "
          "It is not a probability, measured voice accuracy, or model-weight training.")
MAX_TRAITS, MAX_EVIDENCE = 200, 2000


def _now():
    return datetime.now(timezone.utc).isoformat()


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _number(value, name, lower=-1, upper=1):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lower <= value <= upper:
        raise ValueError("%s must be a finite number from %s to %s" % (name, lower, upper))
    return float(value)


def _context(value):
    if not isinstance(value, dict) or not str(value.get("channel", "")).strip():
        raise ValueError("Declare the channel where this preference applies")
    return validate_context(value)


def _matches(declared, requested):
    return lesson_context_matches(declared, requested)


def _source_url(value):
    value = text_field(value, "source_url", 4096)
    if not value:
        return ""
    try:
        url = urlsplit(value)
        if url.scheme not in ("https", "http") or not url.hostname or url.username or url.password:
            raise ValueError()
        # Accessing port rejects malformed ports before persistence.
        url.port
    except ValueError:
        raise ValueError("source_url must be an http(s) URL without credentials")
    return value


def _url_group(value):
    url = urlsplit(value)
    host = (url.hostname or "").lower()
    pairs = parse_qsl(url.query)
    if host in ("youtube.com", "www.youtube.com", "m.youtube.com"):
        video = dict(pairs).get("v", "")
        path = url.path.strip("/").split("/")
        if not video and len(path) == 2 and path[0] in ("shorts", "embed", "live"):
            video = path[1]
        if video:
            return "https://youtube.com/watch?" + urlencode({"v": video})
        pairs = []
    elif host in ("youtu.be", "www.youtu.be") and url.path.strip("/"):
        return "https://youtube.com/watch?" + urlencode({"v": url.path.strip("/").split("/")[0]})
    else:
        pairs = [(key, val) for key, val in pairs if not key.lower().startswith("utm_") and key.lower() not in ("fbclid", "gclid")]
    return urlunsplit((url.scheme.lower(), url.netloc.lower(), url.path, urlencode(sorted(pairs)), ""))


def estimate_evidence(evidence):
    """Pure calculation over already scoped/eligible records; never reads text."""
    active = [item for item in evidence if item.get("status") == "confirmed" and item.get("eligible", True)]
    groups = defaultdict(list)
    for item in active:
        groups[item["group_key"]].append(item)
    mass = total = second = positive = negative = 0.0
    support_groups = oppose_groups = 0
    for items in groups.values():
        weights = [SOURCE_WEIGHTS[item["source_kind"]] * item["reliability"] for item in items]
        denominator = sum(weights)
        if not denominator:
            continue
        group_mass = max(weights)
        mean = sum(weight * item["value"] for weight, item in zip(weights, items)) / denominator
        moment = sum(weight * item["value"] ** 2 for weight, item in zip(weights, items)) / denominator
        pos = sum(weight for weight, item in zip(weights, items) if item["value"] > .05) / denominator
        neg = sum(weight for weight, item in zip(weights, items) if item["value"] < -.05) / denominator
        mass += group_mass
        total += group_mass * mean
        second += group_mass * moment
        positive += group_mass * pos
        negative += group_mass * neg
        support_groups += pos > 0
        oppose_groups += neg > 0
    strength = total / mass if mass else 0.0
    variance = max(0.0, second / mass - strength ** 2) if mass else 0.0
    consistency = max(0.0, 1 - math.sqrt(variance))
    coverage = mass / (mass + 3) if mass else 0.0
    confidence = coverage * consistency
    conflict_ratio = min(positive, negative) / (positive + negative) if positive + negative else 0.0
    disputed = positive > 0 and negative > 0 and conflict_ratio >= .25
    pending = sum(item.get("status") == "pending" for item in evidence)
    inactive = sum(item.get("status") == "confirmed" and not item.get("eligible", True) for item in evidence)
    label = ("disputed" if disputed else "unreviewed" if not mass else
             "supported" if confidence >= .6 and len(groups) >= 3 else
             "developing" if confidence >= .35 and len(groups) >= 2 else "tentative")
    applies = bool(mass and not disputed and abs(strength) >= .1 and confidence >= .15)
    return {"strength": round(strength, 6), "confidence": round(confidence, 6),
            "confidence_label": label, "independent_groups": sum(any(item["reliability"] > 0 for item in group) for group in groups.values()),
            "effective_mass": round(mass, 6), "consistency": round(consistency, 6),
            "supporting_groups": support_groups, "opposing_groups": oppose_groups,
            "conflict_ratio": round(conflict_ratio, 6), "disputed": disputed,
            "pending_evidence": pending, "inactive_sources": inactive,
            "reviewed_evidence": len(active), "applies": applies,
            "influence": round(abs(strength) * confidence, 6) if applies else 0.0,
            "method": METHOD, "notice": NOTICE}


class PreferenceLearner:
    def __init__(self, sqlite_path):
        self.path = Path(sqlite_path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS preference_traits (
                    id TEXT PRIMARY KEY, label TEXT NOT NULL, description TEXT NOT NULL,
                    dimension TEXT NOT NULL, context TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS preference_evidence (
                    id TEXT PRIMARY KEY, trait_id TEXT NOT NULL REFERENCES preference_traits(id),
                    source_kind TEXT NOT NULL, source_id TEXT NOT NULL, source_key TEXT NOT NULL,
                    source_fingerprint TEXT NOT NULL, group_key TEXT NOT NULL,
                    context TEXT NOT NULL, value REAL NOT NULL, reliability REAL NOT NULL,
                    attribution TEXT NOT NULL, notes TEXT NOT NULL, before_text TEXT NOT NULL,
                    after_text TEXT NOT NULL, reference_text TEXT NOT NULL, source_url TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending','confirmed','retracted')),
                    created_at TEXT NOT NULL, reviewed_at TEXT, retracted_at TEXT,
                    UNIQUE(trait_id,source_key));
                CREATE INDEX IF NOT EXISTS preference_evidence_trait ON preference_evidence(trait_id);
                CREATE TABLE IF NOT EXISTS preference_evidence_history (
                    id TEXT PRIMARY KEY, evidence_id TEXT NOT NULL REFERENCES preference_evidence(id),
                    action TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL);
            """)
        self.path.chmod(0o600)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self.path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def _trait(row):
        if row is None:
            raise KeyError("Preference not found")
        item = dict(row)
        item["context"] = json.loads(item["context"])
        item["enabled"] = bool(item["enabled"])
        return item

    def create_trait(self, packet):
        if not isinstance(packet, dict) or set(packet) - {"label", "description", "dimension", "context"}:
            raise ValueError("A preference accepts label, description, dimension and context")
        item = {"label": text_field(packet.get("label", ""), "label", 200, True),
                "description": text_field(packet.get("description", ""), "description", 2000, True),
                "dimension": text_field(packet.get("dimension", ""), "dimension", 80, True),
                "context": _context(packet.get("context"))}
        if item["dimension"] not in DIMENSIONS | {"presentation"}:
            raise ValueError("Unknown preference dimension")
        item.update(id=uuid.uuid4().hex, enabled=True, created_at=_now(), updated_at=_now())
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT COUNT(*) FROM preference_traits").fetchone()[0] >= MAX_TRAITS:
                raise ValueError("Review the existing preferences before adding more")
            conn.execute("INSERT INTO preference_traits VALUES (?,?,?,?,?,?,?,?)", (
                item["id"], item["label"], item["description"], item["dimension"], json.dumps(item["context"]),
                1, item["created_at"], item["updated_at"]))
        return self.get_trait(item["id"])

    def update_trait(self, ident, patch):
        if not isinstance(patch, dict) or not patch or set(patch) - {"label", "enabled"}:
            raise ValueError("Update label or enabled; changing a preference's meaning requires a new preference")
        if "enabled" in patch and not isinstance(patch["enabled"], bool):
            raise ValueError("enabled must be true or false")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            trait = self._trait(conn.execute("SELECT * FROM preference_traits WHERE id=?", (ident,)).fetchone())
            label = text_field(patch.get("label", trait["label"]), "label", 200, True)
            conn.execute("UPDATE preference_traits SET label=?,enabled=?,updated_at=? WHERE id=?",
                         (label, int(patch.get("enabled", trait["enabled"])), _now(), ident))
        return self.get_trait(ident)

    @staticmethod
    def _table(conn, name):
        return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None

    def _source(self, conn, kind, ident):
        """Read immutable provenance; never return unrevealed comparison labels."""
        if kind == "reference":
            if not self._table(conn, "samples"):
                raise ValueError("The selected reference no longer exists")
            row = conn.execute("SELECT * FROM samples WHERE id=?", (ident,)).fetchone()
            if row is None or row["held_out"]:
                raise ValueError("Held-out or missing writing cannot be preference evidence")
            try:
                url = _source_url(row["source_url"])
            except ValueError:
                url = ""  # Imported provenance is not necessarily a navigable URL.
            return {"context": None, "fingerprint": _hash([row["fingerprint"], row["source_url"]]), "group": url or row["fingerprint"],
                    "url": url, "linked_lesson_id": ""}
        if kind == "correction":
            if not self._table(conn, "corrections"):
                raise ValueError("The saved correction no longer exists")
            from .corrections import CorrectionStore
            item, lesson = CorrectionStore._load(conn, ident)
            if item["status"] != "active" or item["kind"] != "style":
                raise ValueError("Only an unchanged active style correction can supply evidence")
            return {"context": item["context"], "fingerprint": item["lesson_fingerprint"],
                    "group": _hash([item["original_text"], item["revised_text"]]), "url": "",
                    "linked_lesson_id": item["lesson_id"]}
        if kind == "practice":
            if not self._table(conn, "feedback") or not self._table(conn, "rounds"):
                raise ValueError("The saved practice feedback no longer exists")
            row = conn.execute("SELECT f.*,r.context,r.options,r.provider FROM feedback f JOIN rounds r ON r.id=f.round_id WHERE f.id=?", (ident,)).fetchone()
            if row is None or row["provider"] not in ("codex", "claude", "anthropic") or row["choice"] not in ("a", "b"):
                raise ValueError("Select saved feedback on a real-model practice choice; offline and neither cannot teach a directional preference")
            choice = next((item["text"] for item in json.loads(row["options"]) if item["id"] == row["choice"]), "")
            group = _hash([choice, row["edited_text"]]) if row["edited_text"] and choice != row["edited_text"] else _hash([row["context"], row["options"]])
            return {"context": json.loads(row["context"]), "fingerprint": _hash(dict(row)), "group": group, "url": "", "linked_lesson_id": ""}
        table = {"quality": "quality_cases", "evaluation": "eval_cases"}.get(kind)
        if not table or not self._table(conn, table):
            raise ValueError("The rated comparison no longer exists")
        row = conn.execute("SELECT * FROM " + table + " WHERE id=?", (ident,)).fetchone()
        if row is None or row["status"] != "rated":
            raise ValueError("Rate the blind comparison before attributing a preference")
        if row["provider"] not in ("codex", "claude", "anthropic"):
            raise ValueError("Offline rehearsal is not preference evidence")
        linked = ""
        if kind == "evaluation" and row["factual_issue"]:
            raise ValueError("A comparison flagged for factual differences cannot teach a style preference")
        if kind == "quality":
            if set(json.loads(row["meaning"]).values()) != {"preserved"}:
                raise ValueError("Both comparison options must preserve meaning before learning style")
            provenance = json.loads(row["provenance"])
            linked = provenance.get("lesson_id", "")
        if row["choice"] in ("tie", "neither"):
            raise ValueError("A tie or neither choice does not identify a directional style preference")
        # The original rating is retained. Attribution never rewrites that vote.
        snapshot = [row["context"], row["options"], row["choice"], row["rated_at"], row["provider"]]
        return {"context": json.loads(row["context"]), "fingerprint": _hash(snapshot),
                "group": _hash([row["context"], row["brief"], row["options"]]), "url": "", "linked_lesson_id": linked}

    def _contains_holdout(self, conn, item):
        # Detect known copies and substantial contiguous excerpts, not paraphrases.
        passages = [" ".join(item[field].casefold().split()) for field in ("before_text", "after_text", "reference_text") if item[field]]
        if not passages or not self._table(conn, "samples"):
            return False
        for row in conn.execute("SELECT text FROM samples WHERE held_out=1"):
            held = " ".join(row[0].casefold().split())
            words = held.split()
            runs = [" ".join(words[index:index + 12]) for index in range(max(0, len(words) - 11))]
            if any(passage == held or (len(held) >= 40 and held in passage)
                   or any(run in passage for run in runs) for passage in passages):
                return True
        return False

    def add_evidence(self, trait_id, packet):
        fields = {"source_kind", "source_id", "batch_key", "context", "value", "reliability", "attribution", "notes",
                  "before_text", "after_text", "reference_text", "source_url", "confirmed", "style_only", "meaning_preserved"}
        if not isinstance(packet, dict) or set(packet) - fields:
            raise ValueError("Unknown preference evidence field")
        kind = packet.get("source_kind")
        if not isinstance(kind, str) or kind not in SOURCE_WEIGHTS:
            raise ValueError("Unknown preference evidence source")
        value = _number(packet.get("value"), "value")
        reliability = _number(packet.get("reliability", 1), "reliability", 0, 1)
        if reliability <= 0:
            raise ValueError("reliability must be greater than zero")
        confirmed = packet.get("confirmed", False)
        if not isinstance(confirmed, bool):
            raise ValueError("confirmed must be true or false")
        item = {key: text_field(packet.get(key, ""), key, limit, key == "attribution")
                for key, limit in (("source_id", 100), ("batch_key", 500), ("attribution", 2000), ("notes", 2000),
                                   ("before_text", 30000), ("after_text", 30000), ("reference_text", 4000))}
        item["source_url"] = _source_url(packet.get("source_url", ""))
        if kind == "edit":
            if not item["before_text"] or not item["after_text"] or item["before_text"] == item["after_text"]:
                raise ValueError("An edit requires different original and revised writing")
            if packet.get("style_only") is not True or packet.get("meaning_preserved") is not True:
                raise ValueError("Confirm that this edit changes style and preserves meaning")
        elif kind == "practice":
            if not item["source_id"] or packet.get("style_only") is not True or packet.get("meaning_preserved") is not True:
                raise ValueError("Select saved practice feedback and confirm that the attributed difference is style-only and preserves meaning")
        elif kind == "reference":
            if not item["source_id"] and not item["reference_text"]:
                raise ValueError("Supply the selected reference or its saved sample ID")
        elif kind == "direct":
            if any(item[key] for key in ("before_text", "after_text", "reference_text", "source_id")):
                raise ValueError("Use the edit or reference source for passages; direct evidence is an explicit preference")
        elif not item["source_id"]:
            raise ValueError("Select the saved correction or rated comparison")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            trait = self._trait(conn.execute("SELECT * FROM preference_traits WHERE id=?", (trait_id,)).fetchone())
            if self._contains_holdout(conn, item):
                raise ValueError("Held-out writing cannot supply preference evidence, including pasted excerpts")
            source = self._source(conn, kind, item["source_id"]) if item["source_id"] else None
            context = _context(packet["context"] if "context" in packet else (source or {}).get("context") or trait["context"])
            if not _matches(trait["context"], context):
                raise ValueError("Evidence must stay within the preference's declared context")
            if source and source.get("context") and not _matches(source["context"], context):
                raise ValueError("Evidence cannot silently broaden the saved source's context")
            url = (source or {}).get("url") or item["source_url"]
            item["source_url"] = url
            group = (source or {}).get("group")
            if kind == "reference" and url:
                group = _url_group(url)
            elif kind == "reference" and not group:
                group = _hash(["reference", " ".join(item["reference_text"].split())])
            if kind == "edit" and not group:
                # A manual edit and the same edit later saved as a correction
                # must not become two independent confirmations.
                group = _hash([item["before_text"], item["after_text"]])
            if not group:
                group = item["batch_key"] or _hash([kind, item["before_text"], item["after_text"],
                                                  item["reference_text"], item["attribution"], context])
            source_key = _hash([kind, item["source_id"]]) if item["source_id"] else _hash([
                kind, item["before_text"], item["after_text"], item["reference_text"], item["attribution"], context])
            payload_hash = _hash([value, reliability, context, item["attribution"], item["notes"]])
            previous = conn.execute("SELECT * FROM preference_evidence WHERE trait_id=? AND source_key=?", (trait_id, source_key)).fetchone()
            replaces = ""
            while previous:
                if _hash([previous["value"], previous["reliability"], json.loads(previous["context"]), previous["attribution"], previous["notes"]]) == payload_hash:
                    return self._evidence(conn, previous)
                if previous["status"] != "retracted":
                    raise ValueError("This source already has an interpretation; withdraw it before recording a revised interpretation")
                # Revisions retain the withdrawn record and its review history.
                # The same source group still caps their combined contribution.
                replaces = previous["id"]
                source_key = _hash([source_key, replaces])
                previous = conn.execute("SELECT * FROM preference_evidence WHERE trait_id=? AND source_key=?", (trait_id, source_key)).fetchone()
            if conn.execute("SELECT COUNT(*) FROM preference_evidence WHERE trait_id=?", (trait_id,)).fetchone()[0] >= MAX_EVIDENCE:
                raise ValueError("Review this preference's existing evidence before adding more")
            item.update(id=uuid.uuid4().hex, trait_id=trait_id, source_kind=kind, source_key=source_key,
                        source_fingerprint=(source or {}).get("fingerprint", ""),
                        group_key=_hash(group), context=context, value=value, reliability=reliability,
                        status="confirmed" if confirmed else "pending", created_at=_now(),
                        reviewed_at=_now() if confirmed else None, retracted_at=None)
            columns = ["id", "trait_id", "source_kind", "source_id", "source_key", "source_fingerprint", "group_key",
                       "context", "value", "reliability", "attribution", "notes", "before_text", "after_text",
                       "reference_text", "source_url", "status", "created_at", "reviewed_at", "retracted_at"]
            encoded = {**item, "context": json.dumps(context)}
            conn.execute("INSERT INTO preference_evidence (" + ",".join(columns) + ") VALUES (" + ",".join("?" for _ in columns) + ")",
                         tuple(encoded[key] for key in columns))
            conn.execute("INSERT INTO preference_evidence_history VALUES (?,?,?,?,?)", (
                uuid.uuid4().hex, item["id"], "confirmed" if confirmed else "proposed",
                "Revises withdrawn interpretation " + replaces if replaces else "Explicitly supplied preference attribution", _now()))
            return self._evidence(conn, conn.execute("SELECT * FROM preference_evidence WHERE id=?", (item["id"],)).fetchone())

    def _evidence(self, conn, row, include_text=True):
        value = dict(row)
        value["context"] = json.loads(value["context"])
        value.update(eligible=True, inactive_reason="", linked_lesson_id="")
        if value["source_id"]:
            try:
                source = self._source(conn, value["source_kind"], value["source_id"])
                if source["fingerprint"] != value["source_fingerprint"]:
                    raise ValueError("The saved source changed after this attribution")
                value["linked_lesson_id"] = source.get("linked_lesson_id", "")
            except (KeyError, ValueError):
                value.update(eligible=False, inactive_reason="The source is no longer eligible; this evidence does not affect the estimate")
        if self._contains_holdout(conn, value):
            value.update(eligible=False, inactive_reason="This passage is now held out; its preference evidence no longer contributes")
        if include_text and value["eligible"] and value["source_kind"] == "reference" and value["source_id"]:
            value["reference_text"] = conn.execute("SELECT text FROM samples WHERE id=?", (value["source_id"],)).fetchone()[0]
        if not include_text:
            for field in ("before_text", "after_text", "reference_text"):
                value.pop(field, None)
        return value

    def review_evidence(self, ident, action, reason=""):
        if action not in ("confirm", "retract", "restore"):
            raise ValueError("Choose confirm, retract or restore")
        reason = text_field(reason, "reason", 2000, True)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM preference_evidence WHERE id=?", (ident,)).fetchone()
            if row is None:
                raise KeyError("Preference evidence not found")
            item = self._evidence(conn, row)
            if action == "confirm" and item["status"] != "pending":
                raise ValueError("Only pending interpretations can be confirmed")
            if action == "restore" and (item["status"] != "retracted" or not item["reviewed_at"]):
                raise ValueError("Only previously reviewed retracted evidence can be restored")
            if action in ("confirm", "restore") and not item["eligible"]:
                raise ValueError(item["inactive_reason"])
            status = "retracted" if action == "retract" else "confirmed"
            if item["status"] == status:
                return item
            conn.execute("UPDATE preference_evidence SET status=?,reviewed_at=?,retracted_at=? WHERE id=?", (
                status, item["reviewed_at"] or (_now() if status == "confirmed" else None),
                _now() if status == "retracted" else None, ident))
            conn.execute("INSERT INTO preference_evidence_history VALUES (?,?,?,?,?)", (
                uuid.uuid4().hex, ident, action, reason, _now()))
            return self._evidence(conn, conn.execute("SELECT * FROM preference_evidence WHERE id=?", (ident,)).fetchone())

    def get_trait(self, ident, context=None, include_evidence=True):
        with self._connect() as conn:
            trait = self._trait(conn.execute("SELECT * FROM preference_traits WHERE id=?", (ident,)).fetchone())
            requested = _context(context) if context is not None else trait["context"]
            scoped = _matches(trait["context"], requested)
            evidence = [self._evidence(conn, row, include_text=False) for row in conn.execute(
                "SELECT * FROM preference_evidence WHERE trait_id=? ORDER BY created_at,id", (ident,))]
            relevant = [item for item in evidence if scoped and _matches(item["context"], requested)]
            trait["estimate"] = estimate_evidence(relevant)
            trait["estimate"]["applies"] &= trait["enabled"] and scoped
            if not trait["estimate"]["applies"]:
                trait["estimate"]["influence"] = 0.0
            trait["estimate_context"] = requested
            trait["out_of_scope_evidence"] = len(evidence) - len(relevant)
            trait["total_evidence"] = len(evidence)
            if include_evidence:
                trait["evidence"] = [self._evidence(conn, row) for row in conn.execute(
                    "SELECT * FROM preference_evidence WHERE trait_id=? ORDER BY created_at,id LIMIT 50", (ident,))]
                trait["evidence_truncated"] = len(evidence) > 50
                trait["history"] = [dict(row) for row in conn.execute(
                    "SELECT h.* FROM preference_evidence_history h JOIN preference_evidence e ON e.id=h.evidence_id WHERE e.trait_id=? ORDER BY h.created_at DESC,h.id DESC LIMIT 100", (ident,))]
            return trait

    def list_evidence(self, trait_id, limit=50, offset=0):
        for value, name, upper in ((limit, "limit", 100), (offset, "offset", MAX_EVIDENCE)):
            if isinstance(value, bool) or not isinstance(value, int) or not (1 if name == "limit" else 0) <= value <= upper:
                raise ValueError("Invalid evidence " + name)
        with self._connect() as conn:
            self._trait(conn.execute("SELECT * FROM preference_traits WHERE id=?", (trait_id,)).fetchone())
            total = conn.execute("SELECT COUNT(*) FROM preference_evidence WHERE trait_id=?", (trait_id,)).fetchone()[0]
            items = [self._evidence(conn, row) for row in conn.execute(
                "SELECT * FROM preference_evidence WHERE trait_id=? ORDER BY created_at,id LIMIT ? OFFSET ?", (trait_id, limit, offset))]
            return {"items": items, "total": total, "offset": offset,
                    "next_offset": offset + len(items) if offset + len(items) < total else None}

    def list_traits(self, context=None, include_evidence=False):
        with self._connect() as conn:
            ids = [row[0] for row in conn.execute("SELECT id FROM preference_traits ORDER BY created_at,id")]
        return [self.get_trait(ident, context, include_evidence) for ident in ids]

    def resolve(self, context, exclude_source_ids=()):
        requested = _context(context)
        if not isinstance(exclude_source_ids, (list, tuple)) or len(exclude_source_ids) > 200:
            raise ValueError("exclude_source_ids must be a bounded list of source IDs")
        exclude_source_ids = [text_field(value, "excluded source id", 100, True) for value in exclude_source_ids]
        excluded = set(exclude_source_ids)
        excluded_groups, excluded_lessons = set(), set()
        with self._connect() as conn:
            for ident in excluded:
                try:
                    source = self._source(conn, "correction", ident)
                    excluded_groups.add(_hash(source["group"]))
                    excluded_lessons.add(source["linked_lesson_id"])
                except (ValueError, KeyError):
                    pass
        candidates = self.list_traits(requested, include_evidence=False)
        selected, warnings = [], []
        for trait in candidates:
            with self._connect() as conn:
                evidence = [self._evidence(conn, row, include_text=False) for row in conn.execute(
                    "SELECT * FROM preference_evidence WHERE trait_id=? ORDER BY created_at,id", (trait["id"],))]
            relevant = [item for item in evidence if _matches(item["context"], requested) and item["source_id"] not in excluded
                        and item["group_key"] not in excluded_groups and item["linked_lesson_id"] not in excluded_lessons]
            estimate = estimate_evidence(relevant)
            if not trait["enabled"] or not _matches(trait["context"], requested):
                continue
            if estimate["disputed"]:
                warnings.append("%s has conflicting evidence and is not applied" % trait["label"])
            if not estimate["applies"]:
                continue
            selected.append({"id": trait["id"], "label": trait["label"], "description": trait["description"],
                             "dimension": trait["dimension"], "context": trait["context"], "estimate": estimate,
                             "evidence_ids": [item["id"] for item in relevant if item["status"] == "confirmed" and item["eligible"]],
                             "linked_lesson_ids": sorted({item["linked_lesson_id"] for item in relevant if item["status"] == "confirmed" and item["eligible"] and item["linked_lesson_id"]})})
        selected.sort(key=lambda item: (-item["estimate"]["influence"], item["id"]))
        return {"context": requested, "preferences": selected[:24], "warnings": warnings,
                "omitted": max(0, len(selected) - 24), "method": METHOD, "notice": NOTICE}

    def guidance(self, context, exclude_source_ids=()):
        return preference_guidance(self.resolve(context, exclude_source_ids)["preferences"])

    def export_preferences(self):
        """Scoped estimates only; raw sources and pending interpretations stay local."""
        return preference_guidance([item for item in self.list_traits() if item["enabled"] and item["estimate"]["applies"]])


def preference_guidance(preferences):
    return [{**{key: item[key] for key in ("id", "label", "description", "dimension", "context", "estimate")},
                 "evidence_ids": item.get("evidence_ids", []), "linked_lesson_ids": item.get("linked_lesson_ids", []),
                 "is_preference": True,
                 "guidance": ("Supporting preference: %s. %s Direction: %s; strength %.2f; evidence confidence %.2f (%s). "
                              "Use proportionately where relevant; explicit instructions and context-specific direct corrections take priority. "
                              "Generalize the reviewed quality to analogous writing; the example words are evidence, not a required phrase. Allow fresh expression that preserves the intended degree and attitude; do not add emphasis, slang, reactions or facts to meet a quota. Do not imitate a source passage or add its facts." % (
                                  item["label"], item["description"], "toward" if item["estimate"]["strength"] > 0 else "away from",
                                  abs(item["estimate"]["strength"]), item["estimate"]["confidence"], item["estimate"]["confidence_label"]))}
                for item in preferences]
