"""Fixed evaluation panels: does calibration help on *your* real situations?

A panel is a small, fixed set of real situations (context + factual brief),
balanced across the channels you care about. Each run turns every item into an
ordinary blind baseline-vs-calibrated evaluation case, which you rate on the
Check fit page. The report shows outcomes per channel and per run, so you can
repeat the same panel after a defined batch of feedback and compare.

Guards (see docs/PRODUCT.md "Evaluation before bigger training"):
- A brief that duplicates or heavily overlaps a confirmed, non-held-out sample
  is refused: that sample would be fed to the calibrated draft as an example.
  Mark it held out (or rewrite the brief) first.
- Items are frozen once created; a panel is compared only with itself.
- Runs record a snapshot of what the profile contained, never sample text.
- Offline runs are labelled rehearsal and excluded from live results.

Usage:
  python3 -m voicework.panel create panel.json
  python3 -m voicework.panel list
  python3 -m voicework.panel run NAME --provider codex|anthropic [--model M] [--codex-bin PATH] [--label after-batch-1]
  python3 -m voicework.panel run NAME --resume          # finish a partial run
  python3 -m voicework.panel report NAME [--json]
"""
import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import uuid
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .context import PriorStore
from .evaluation import EvaluationStore
from .guidance import writing_inputs
from .language import LanguageLessonStore
from .learning import validate_context
from .matching import normalize_label
from .store import Store, text_field

MAX_ITEMS = 60
MIN_PER_CHANNEL = 3
_WORD = re.compile(r"[\w’']+")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _words(text):
    return {word.lower() for word in _WORD.findall(text) if len(word) > 3}


def holdout_conflicts(brief, samples, threshold=0.6):
    """Return IDs of training-eligible samples a brief duplicates or heavily overlaps."""
    brief_norm = " ".join(brief.split()).lower()
    brief_words = _words(brief)
    conflicts = []
    for sample in samples:
        if sample.get("status") != "confirmed" or sample.get("held_out"):
            continue
        text = sample.get("text", "")
        norm = " ".join(text.split()).lower()
        if brief_norm and (brief_norm == norm or (len(brief_norm) >= 80 and (brief_norm in norm or norm in brief_norm))):
            conflicts.append(sample["id"])
            continue
        if len(brief_words) >= 12 and len(brief_words & _words(text)) / len(brief_words) >= threshold:
            conflicts.append(sample["id"])
    return conflicts


def balance_warnings(items):
    channels = Counter(normalize_label("channel", item["context"]["channel"]) for item in items)
    warnings = []
    for channel, count in sorted(channels.items()):
        if count < MIN_PER_CHANNEL:
            warnings.append("Only %d item(s) for %s; at least %d per channel gives a less noisy direction." % (count, channel, MIN_PER_CHANNEL))
    if len(channels) > 1 and max(channels.values()) > 2 * min(channels.values()):
        warnings.append("Channels are unbalanced (%s); the overall result will mostly reflect the largest channel." %
                        ", ".join("%s=%d" % pair for pair in sorted(channels.items())))
    return warnings


class PanelStore:
    def __init__(self, sqlite_path):
        self.path = Path(sqlite_path)
        EvaluationStore(self.path)  # reports join eval_cases; also applies its provider migration
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS eval_panels (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS eval_panel_items (
                    id TEXT PRIMARY KEY, panel_id TEXT NOT NULL, position INTEGER NOT NULL,
                    context TEXT NOT NULL, brief TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS eval_panel_runs (
                    id TEXT PRIMARY KEY, panel_id TEXT NOT NULL, label TEXT NOT NULL, provider TEXT NOT NULL,
                    model TEXT NOT NULL, snapshot TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS eval_panel_cases (
                    run_id TEXT NOT NULL, item_id TEXT NOT NULL, case_id TEXT, error TEXT, created_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, item_id));
            """)
        self.path.chmod(0o600)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self.path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def create(self, definition, samples):
        if not isinstance(definition, dict) or set(definition) - {"name", "items", "description"}:
            raise ValueError("A panel needs name and items (and an optional description)")
        name = text_field(definition.get("name", ""), "name", 100, True)
        raw = definition.get("items")
        if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_ITEMS:
            raise ValueError("items must list 1 to %d situations" % MAX_ITEMS)
        items, seen = [], set()
        for index, item in enumerate(raw, 1):
            if not isinstance(item, dict) or set(item) - {"context", "brief"}:
                raise ValueError("Item %d needs only context and brief" % index)
            context = validate_context(item.get("context"))
            brief = text_field(item.get("brief", ""), "brief", 15000, True)
            key = " ".join(brief.split()).lower()
            if key in seen:
                raise ValueError("Item %d repeats an earlier brief" % index)
            seen.add(key)
            conflicts = holdout_conflicts(brief, samples)
            if conflicts:
                raise ValueError("Item %d overlaps confirmed training sample(s) %s. Mark them held out in Voicework or rewrite the brief; "
                                 "otherwise the calibrated draft would see the answer." % (index, ", ".join(conflicts[:5])))
            items.append({"context": context, "brief": brief})
        panel_id = uuid.uuid4().hex
        with self._connect() as conn:
            if conn.execute("SELECT 1 FROM eval_panels WHERE name=?", (name,)).fetchone():
                raise ValueError("A panel named %r already exists; panels are frozen, so create a new name" % name)
            conn.execute("INSERT INTO eval_panels VALUES (?,?,?)", (panel_id, name, _now()))
            conn.executemany("INSERT INTO eval_panel_items VALUES (?,?,?,?,?)", [
                (uuid.uuid4().hex, panel_id, position, json.dumps(item["context"]), item["brief"]) for position, item in enumerate(items)])
        return {"id": panel_id, "name": name, "items": len(items), "warnings": balance_warnings(items)}

    def panels(self):
        with self._connect() as conn:
            rows = conn.execute("""SELECT p.id, p.name, p.created_at,
                (SELECT COUNT(*) FROM eval_panel_items i WHERE i.panel_id=p.id) AS items,
                (SELECT COUNT(*) FROM eval_panel_runs r WHERE r.panel_id=p.id) AS runs
                FROM eval_panels p ORDER BY p.created_at""").fetchall()
        return [dict(row) for row in rows]

    def panel(self, name):
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM eval_panels WHERE name=?", (name,)).fetchone()
            if row is None:
                raise KeyError("No panel named %r" % name)
            items = [{"id": r["id"], "position": r["position"], "context": json.loads(r["context"]), "brief": r["brief"]}
                     for r in conn.execute("SELECT * FROM eval_panel_items WHERE panel_id=? ORDER BY position", (row["id"],))]
        return {**dict(row), "items": items}

    def start_run(self, panel, label, provider, snapshot):
        run_id = uuid.uuid4().hex
        with self._connect() as conn:
            label = text_field(label or "run %d" % (1 + conn.execute("SELECT COUNT(*) FROM eval_panel_runs WHERE panel_id=?", (panel["id"],)).fetchone()[0]), "label", 100, True)
            conn.execute("INSERT INTO eval_panel_runs VALUES (?,?,?,?,?,?,?)", (
                run_id, panel["id"], label, provider.mode, getattr(provider, "model", "") or "", json.dumps(snapshot), _now()))
        return run_id, label

    def latest_run(self, panel):
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM eval_panel_runs WHERE panel_id=? ORDER BY created_at DESC LIMIT 1", (panel["id"],)).fetchone()
        return dict(row) if row else None

    def done_items(self, run_id):
        with self._connect() as conn:
            return {row["item_id"] for row in conn.execute("SELECT item_id FROM eval_panel_cases WHERE run_id=? AND case_id IS NOT NULL", (run_id,))}

    def record(self, run_id, item_id, case_id=None, error=None):
        with self._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO eval_panel_cases VALUES (?,?,?,?,?)", (run_id, item_id, case_id, error, _now()))

    def report(self, name):
        panel = self.panel(name)
        with self._connect() as conn:
            runs = [dict(r) for r in conn.execute("SELECT * FROM eval_panel_runs WHERE panel_id=? ORDER BY created_at", (panel["id"],))]
            result = []
            for run in runs:
                rows = conn.execute("""SELECT c.item_id, c.case_id, c.error, e.context, e.provider, e.status, e.choice, e.mapping, e.factual_issue
                    FROM eval_panel_cases c LEFT JOIN eval_cases e ON e.id = c.case_id WHERE c.run_id=?""", (run["id"],)).fetchall()
                channels = {}
                totals = _empty()
                for row in rows:
                    item = next((i for i in panel["items"] if i["id"] == row["item_id"]), None)
                    channel = normalize_label("channel", (item or {}).get("context", {}).get("channel", "work"))
                    bucket = channels.setdefault(channel, _empty())
                    for target in (bucket, totals):
                        _tally(target, row)
                missing = len(panel["items"]) - len(rows)
                totals["not_started"] += missing
                result.append({"label": run["label"], "provider": run["provider"], "model": run["model"], "created_at": run["created_at"],
                               "snapshot": json.loads(run["snapshot"]), "totals": totals,
                               "per_channel": [{"channel": c, **counts} for c, counts in sorted(channels.items())]})
        return {"panel": panel["name"], "items": len(panel["items"]),
                "balance_warnings": balance_warnings(panel["items"]), "runs": result,
                "notice": "Your blind judgments on this fixed panel. A small panel gives direction, not a reliable accuracy rate; "
                          "a preference win and a factual problem can coexist."}


def _empty():
    return {"cases": 0, "unrated": 0, "failed": 0, "not_started": 0, "rated": 0, "calibrated": 0, "baseline": 0,
            "tie": 0, "neither": 0, "factual_flags": 0, "rehearsal": 0}


def _tally(target, row):
    if row["case_id"] is None:
        target["failed"] += 1
        return
    target["cases"] += 1
    if row["provider"] == "offline":
        target["rehearsal"] += 1
        return
    if row["status"] != "rated":
        target["unrated"] += 1
        return
    target["rated"] += 1
    target["factual_flags"] += bool(row["factual_issue"])
    target[json.loads(row["mapping"]).get(row["choice"], row["choice"])] += 1


def snapshot(samples, feedback, lessons, priors, corrections_active):
    """Describe what the profile contained when a run started (IDs hashed, never text)."""
    training = sorted(s["id"] for s in samples if s.get("status") == "confirmed" and not s.get("held_out"))
    enabled = sorted(l["id"] for l in lessons if l.get("enabled") is True)
    digest = hashlib.sha256(json.dumps([training, enabled, sorted(f["id"] for f in feedback), sorted(p["id"] for p in priors)]).encode()).hexdigest()[:16]
    return {"training_samples": len(training), "held_out_samples": sum(bool(s.get("held_out")) for s in samples if s.get("status") == "confirmed"),
            "enabled_lessons": len(enabled), "feedback": len(feedback), "confirmed_priors": len(priors),
            "active_corrections": corrections_active, "profile_digest": digest}


def run_panel(path, name, provider, label=None, resume=False, log=None):
    from .corrections import CorrectionStore
    log = log or (lambda message: print(message, file=sys.stderr, flush=True))
    store, panels = Store(path), PanelStore(path)
    lessons_store, priors_store, evaluations = LanguageLessonStore(path), PriorStore(path), EvaluationStore(path)
    panel = panels.panel(name)
    samples, feedback = store.samples(), store.feedback()
    lessons, priors = lessons_store.list(), priors_store.list(confirmed_only=True)
    for item in panel["items"]:
        if holdout_conflicts(item["brief"], samples):
            raise ValueError("Item %d now overlaps a confirmed training sample (it was confirmed after the panel was created). "
                             "Mark it held out before running." % (item["position"] + 1))
    if resume:
        run = panels.latest_run(panel)
        if run is None:
            raise ValueError("No run to resume")
        if run["provider"] != provider.mode:
            raise ValueError("Resume with the same provider as the run (%s)" % run["provider"])
        run_id, label = run["id"], run["label"]
    else:
        active = sum(c.get("status") == "active" for c in CorrectionStore(path).list())
        run_id, label = panels.start_run(panel, label, provider, snapshot(samples, feedback, lessons, priors, active))
    done = panels.done_items(run_id)
    todo = [item for item in panel["items"] if item["id"] not in done]
    log("Panel %r, %s: %d of %d situations to generate with %s." % (name, label, len(todo), len(panel["items"]), provider.mode))
    failures = 0
    for count, item in enumerate(todo, 1):
        context, evidence, preferences, _ = writing_inputs(samples, feedback, lessons, priors, item["context"])
        try:
            case = evaluations.create_case(context, item["brief"], provider, evidence, preferences)
        except (ValueError, OSError) as exc:
            failures += 1
            panels.record(run_id, item["id"], error=str(exc)[:500])
            log("  [%d/%d] failed: %s" % (count, len(todo), exc))
            continue
        panels.record(run_id, item["id"], case_id=case["id"])
        log("  [%d/%d] ready for blind rating (%s)" % (count, len(todo), context.get("channel")))
    log("Done. Rate the new comparisons in Voicework → Check fit → Compare your whole voice profile." +
        (" %d failed; rerun with --resume." % failures if failures else ""))
    return {"run_id": run_id, "label": label, "generated": len(todo) - failures, "failed": failures}


def _format_report(report):
    lines = ["Panel %s — %d situations" % (report["panel"], report["items"]), report["notice"]]
    lines += ["! " + w for w in report["balance_warnings"]]
    if not report["runs"]:
        lines.append("No runs yet.")
    for run in report["runs"]:
        t, s = run["totals"], run["snapshot"]
        lines += ["", "%s · %s %s · %s" % (run["label"], run["provider"], run["model"], run["created_at"][:16]),
                  "  profile: %d training samples, %d lessons, %d feedback, %d corrections (digest %s)" % (
                      s["training_samples"], s["enabled_lessons"], s["feedback"], s["active_corrections"], s["profile_digest"]),
                  "  rated %d/%d · calibrated %d · baseline %d · tie %d · neither %d · factual flags %d%s%s" % (
                      t["rated"], t["cases"], t["calibrated"], t["baseline"], t["tie"], t["neither"], t["factual_flags"],
                      " · failed %d" % t["failed"] if t["failed"] else "", " · rehearsal only" if t["rehearsal"] else "")]
        for c in run["per_channel"]:
            lines.append("    %-20s rated %d/%d  calibrated %d  baseline %d  tie %d  neither %d  factual %d" % (
                c["channel"], c["rated"], c["cases"], c["calibrated"], c["baseline"], c["tie"], c["neither"], c["factual_flags"]))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Fixed blind evaluation panels for Voicework")
    parser.add_argument("--data-dir", default=os.environ.get("VOICEWORK_DATA_DIR", str(Path(__file__).resolve().parent.parent / "data")))
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create", help="Create a frozen panel from a JSON file")
    create.add_argument("file")
    sub.add_parser("list", help="List panels")
    run = sub.add_parser("run", help="Generate blind comparisons for every item")
    run.add_argument("name")
    run.add_argument("--provider", choices=("claude", "codex", "anthropic", "offline"), default=None)
    run.add_argument("--model")
    run.add_argument("--codex-bin")
    run.add_argument("--claude-bin")
    run.add_argument("--label")
    run.add_argument("--resume", action="store_true")
    report = sub.add_parser("report", help="Per-run, per-channel outcomes")
    report.add_argument("name")
    report.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    path = Path(args.data_dir) / "voicework.sqlite3"
    try:
        if not path.is_file():
            raise ValueError("No Voicework database at %s; pass --data-dir" % path)
        panels = PanelStore(path)
        if args.command == "create":
            definition = json.loads(Path(args.file).read_text(encoding="utf-8"))
            result = panels.create(definition, Store(path).samples())
            print("Created panel %r with %d situations." % (result["name"], result["items"]))
            for warning in result["warnings"]:
                print("note: " + warning, file=sys.stderr)
        elif args.command == "list":
            for item in panels.panels():
                print("%s  items=%d runs=%d  created %s" % (item["name"], item["items"], item["runs"], item["created_at"][:10]))
        elif args.command == "run":
            if not args.provider:
                raise ValueError("Choose --provider claude, codex or anthropic (offline creates rehearsal cases only)")
            from .provider import Provider
            provider = Provider(mode=args.provider, model=args.model, executable=args.claude_bin if args.provider == "claude" else args.codex_bin)
            run_panel(path, args.name, provider, args.label, args.resume)
        else:
            result = panels.report(args.name)
            print(json.dumps(result, indent=2) if args.json else _format_report(result))
        return 0
    except KeyError as exc:
        print("panel: %s" % exc.args[0], file=sys.stderr)
        return 2
    except (ValueError, OSError) as exc:
        print("panel: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
