import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from voicework import panel as panel_cli
from voicework.evaluation import EvaluationStore
from voicework.language import LanguageLessonStore
from voicework.panel import PanelStore, balance_warnings, holdout_conflicts, run_panel
from voicework.store import Store


class FakeProvider:
    def __init__(self, mode="anthropic", fail_on=None):
        self.mode, self.model, self.fail_on, self.calls = mode, "test-model", fail_on, []

    def generate_draft(self, context, brief, controls, evidence, preferences):
        self.calls.append({"brief": brief, "evidence": [e["id"] for e in evidence], "lessons": [p["id"] for p in preferences if p.get("is_language_lesson")]})
        if self.fail_on and self.fail_on in brief:
            raise ValueError("synthetic provider failure")
        return {"text": "Draft for %s (%d examples)" % (brief[:20], len(evidence)), "provider": self.mode, "warnings": []}


def items(channel, count, prefix):
    return [{"context": {"channel": channel}, "brief": "%s situation %d: tell the team the fictional launch moved." % (prefix, i)} for i in range(count)]


class PanelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "voicework.sqlite3"
        self.store = Store(self.path)
        sample = self.store.add_sample({"title": "Synthetic", "context": {"channel": "work"},
                                        "text": "The fictional garden shed inventory is finished, the paint order went out Tuesday, and the volunteers meet Saturday morning at nine."})
        self.sample_id = sample["id"]
        self.store.update_sample(self.sample_id, {"status": "confirmed"})
        LanguageLessonStore(self.path).add({"context": {"channel": "work"}, "dimension": "diction", "guidance": "Plain verbs."})
        self.panels = PanelStore(self.path)

    def test_holdout_guard_blocks_overlapping_briefs_until_sample_is_held_out(self):
        overlapping = "The fictional garden shed inventory is finished and the paint order went out Tuesday; volunteers meet Saturday morning."
        self.assertEqual(holdout_conflicts(overlapping, self.store.samples()), [self.sample_id])
        with self.assertRaisesRegex(ValueError, "held out"):
            self.panels.create({"name": "p", "items": [{"context": {"channel": "work"}, "brief": overlapping}]}, self.store.samples())
        self.store.update_sample(self.sample_id, {"held_out": True})
        self.assertEqual(holdout_conflicts(overlapping, self.store.samples()), [])
        self.panels.create({"name": "p", "items": [{"context": {"channel": "work"}, "brief": overlapping}]}, self.store.samples())
        self.assertEqual(holdout_conflicts("A short unrelated note.", self.store.samples()), [])

    def test_validation_and_frozen_names(self):
        bad = [{"items": []}, {"name": "x", "items": [{"context": {"channel": "work"}}]}, {"name": "x", "items": [{"context": {"channel": "work"}, "brief": "same"}, {"context": {"channel": "work"}, "brief": " same "}]},
               {"name": "x", "items": [{"context": {"channel": "work"}, "brief": "b", "answer": "leak"}]}]
        for definition in bad:
            with self.assertRaises(ValueError):
                self.panels.create(definition, [])
        self.panels.create({"name": "x", "items": items("work", 3, "w")}, [])
        with self.assertRaisesRegex(ValueError, "frozen"):
            self.panels.create({"name": "x", "items": items("work", 3, "v")}, [])

    def test_balance_warnings(self):
        warnings = balance_warnings([{"context": {"channel": "work"}}] * 7 + [{"context": {"channel": "personal"}}] * 2)
        self.assertEqual(len(warnings), 2)
        self.assertEqual(balance_warnings([{"context": {"channel": "work"}}] * 3 + [{"context": {"channel": "personal"}}] * 3), [])

    def test_run_resume_and_per_channel_report(self):
        self.panels.create({"name": "oct", "items": items("work", 3, "w") + items("personal", 3, "p")}, self.store.samples())
        provider = FakeProvider(fail_on="p situation 1")
        result = run_panel(self.path, "oct", provider, label="before", log=lambda m: None)
        self.assertEqual((result["generated"], result["failed"]), (5, 1))
        # Baseline draft gets nothing; calibrated draft gets the work lesson and sample only for work items.
        work_calls = [c for c in provider.calls if c["brief"].startswith("w ")]
        self.assertTrue(any(c["evidence"] == [self.sample_id] for c in work_calls))
        self.assertTrue(all(c["evidence"] == [] and c["lessons"] == [] for c in provider.calls[0::2]))
        report = self.panels.report("oct")
        self.assertEqual(report["runs"][0]["totals"]["failed"], 1)
        provider.fail_on = None
        result = run_panel(self.path, "oct", provider, resume=True, log=lambda m: None)
        self.assertEqual((result["generated"], result["failed"]), (1, 0))
        evaluations = EvaluationStore(self.path)
        cases = evaluations.list_cases()
        self.assertEqual(len(cases), 6)
        for case in cases:
            evaluations.rate_case(case["id"], "a")
        report = self.panels.report("oct")
        run = report["runs"][0]
        self.assertEqual(run["label"], "before")
        self.assertEqual(run["totals"]["rated"], 6)
        self.assertEqual(run["totals"]["calibrated"] + run["totals"]["baseline"], 6)
        self.assertEqual([c["channel"] for c in run["per_channel"]], ["personal", "work"])
        self.assertEqual(run["snapshot"]["enabled_lessons"], 1)
        self.assertNotIn("garden", json.dumps(report))
        with self.assertRaisesRegex(ValueError, "same provider"):
            run_panel(self.path, "oct", FakeProvider(mode="codex"), resume=True, log=lambda m: None)

    def test_offline_runs_are_rehearsal(self):
        self.panels.create({"name": "r", "items": items("work", 3, "w")}, [])
        run_panel(self.path, "r", FakeProvider(mode="offline"), log=lambda m: None)
        totals = self.panels.report("r")["runs"][0]["totals"]
        self.assertEqual((totals["rehearsal"], totals["rated"]), (3, 0))

    def test_cli_create_list_report(self):
        definition = Path(self.temp.name) / "panel.json"
        definition.write_text(json.dumps({"name": "cli", "items": items("work", 2, "w")}))
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(panel_cli.main(["--data-dir", self.temp.name, "create", str(definition)]), 0)
            self.assertEqual(panel_cli.main(["--data-dir", self.temp.name, "list"]), 0)
            self.assertEqual(panel_cli.main(["--data-dir", self.temp.name, "report", "cli"]), 0)
            self.assertEqual(panel_cli.main(["--data-dir", self.temp.name, "report", "missing"]), 2)
            self.assertEqual(panel_cli.main(["--data-dir", self.temp.name, "run", "cli"]), 2)
        self.assertIn("Created panel 'cli'", out.getvalue())
        self.assertIn("No runs yet.", out.getvalue())
        self.assertIn("Only 2 item(s)", err.getvalue())


if __name__ == "__main__":
    unittest.main()
