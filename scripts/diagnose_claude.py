#!/usr/bin/env python3
"""Find out why the Claude provider fails, using only a throwaway synthetic prompt.

Runs the Claude Code CLI with progressively more of Voicework's flags and records
exit codes, error fields and short error text. No profile data is used.
Writes work/claude-diagnose.json (git-ignored) and prints a summary.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from voicework.provider import CLAUDE_SYSTEM_PROMPT, _schema  # noqa: E402

exe = sys.argv[1] if len(sys.argv) > 1 else (os.environ.get("VOICEWORK_CLAUDE_BIN") or shutil.which("claude") or "claude")
prompt = "Write one friendly sentence confirming a synthetic meeting moved to Thursday. Return it in the text field with an empty warnings list."
schema = json.dumps(_schema("draft"))
base = [exe, "-p"]
steps = [
    ("version", [exe, "--version"], None),
    ("auth_status", [exe, "auth", "status"], None),
    ("plain_text", base, None),
    ("json_output", base + ["--output-format", "json"], None),
    ("safe_mode", base + ["--output-format", "json", "--safe-mode"], None),
    ("model_opus_5_5", base + ["--output-format", "json", "--safe-mode", "--model", "claude-opus-5-5"], None),
    ("model_alias_opus", base + ["--output-format", "json", "--safe-mode", "--model", "opus"], None),
    ("json_schema", base + ["--output-format", "json", "--safe-mode", "--json-schema", schema], None),
    ("no_tools", base + ["--output-format", "json", "--safe-mode", "--json-schema", schema, "--tools", ""], None),
    ("full_voicework", base + ["--safe-mode", "--output-format", "json", "--json-schema", schema, "--model", "claude-opus-5-5",
                               "--tools", "", "--disallowedTools", "mcp__*", "--strict-mcp-config", "--disable-slash-commands",
                               "--no-session-persistence", "--permission-mode", "dontAsk", "--system-prompt", CLAUDE_SYSTEM_PROMPT], None),
]
report = {"executable": exe, "resolved": shutil.which(exe), "env_has_ANTHROPIC_API_KEY": bool(os.environ.get("ANTHROPIC_API_KEY")), "steps": []}
for name, args, _ in steps:
    with tempfile.TemporaryDirectory() as folder:
        try:
            done = subprocess.run(args, input=None if name in ("version", "auth_status") else prompt, capture_output=True, text=True, timeout=180, cwd=folder)
        except Exception as exc:  # record and continue
            report["steps"].append({"step": name, "error": repr(exc)[:300]})
            continue
    entry = {"step": name, "exit": done.returncode, "stderr": done.stderr.strip()[-400:]}
    try:
        data = json.loads(done.stdout)
        keep = ("is_error", "subtype", "terminal_reason", "api_error_status", "stop_reason", "num_turns", "authMethod", "loggedIn", "apiProvider")
        entry["json"] = {k: data.get(k) for k in keep if k in data}
        entry["result_excerpt"] = str(data.get("result", ""))[:300]
        if "structured_output" in data:
            entry["structured_output"] = data["structured_output"]
        entry["model_usage"] = list((data.get("modelUsage") or {}).keys())
    except ValueError:
        entry["stdout_excerpt"] = done.stdout.strip()[:300]
    report["steps"].append(entry)
    print("%-16s exit=%s %s" % (name, entry.get("exit"), json.dumps(entry.get("json") or entry.get("stdout_excerpt") or entry.get("error"))[:200]), flush=True)
out = ROOT / "work" / "claude-diagnose.json"
out.parent.mkdir(exist_ok=True)
out.write_text(json.dumps(report, indent=2))
print("Wrote", out)
