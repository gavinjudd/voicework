# Voicework

A local writing practice studio that learns how you write **for a particular audience, situation, and purpose**—including how you present it.

Start without samples: teach a language rule, name phrases you prefer or avoid, and compare drafts for a concrete situation. Optionally add reviewed writing to ground the profile in authentic examples. Keep a portable reference for vernacular, sentence structure, word choice, rhythm, and presentation. Personal texting and professional writing retain separate contextual evidence.

Source is published in the private [gavinjudd/voicework repository](https://github.com/gavinjudd/voicework).

## Run

Requires Python 3.9+; no Python or JavaScript packages are needed for the app.

```sh
cd /path/to/voicework
python3 -m voicework --provider codex --model gpt-6-astra
```

Open **http://127.0.0.1:8765**. Codex mode requires a [Codex CLI](https://learn.chatgpt.com/docs/developer-commands) compatible with the selected model, a working login, model access, and network access. The adapter requests `xhigh` reasoning. It uses your local CLI authentication; the app never asks for a password or API key. Select an executable with `--codex-bin`, or set `VOICEWORK_CODEX_BIN`; the flag takes priority, then the environment variable, then `codex` on PATH.

On the development Mac, PATH's CLI 0.137.0 rejected Astra because it required a newer CLI. The app-bundled CLI 0.162.0-alpha.17.2 passed a synthetic live draft with Astra and `xhigh`. This was a compatibility issue, not an authentication failure. This Mac can use:

```sh
python3 -m voicework --provider codex --model gpt-6-astra \
  --codex-bin "/Applications/Codex.app/Contents/Resources/codex-cli/bin/codex"
```

That path is specific to this installation. The smoke test verifies live generation, not personalized writing accuracy. The Chrome extension has not been verified installed in the user's browser. See [VERIFICATION.md](docs/VERIFICATION.md) for the recorded checks.

For a completely offline walkthrough of the workflow:

```sh
python3 -m voicework --provider offline
```

Offline mode is explicitly labeled **rehearsal**. It persists lessons, review and calibration feedback, but uses simple text transformations and fixed comparison contrasts instead of an AI. It does not semantically apply language lessons or demonstrate personalized writing quality. There is no silent fallback from Codex to offline.

## First useful session

1. **Teach directly:** add a context-specific language lesson with a rule and preferred or avoided phrasing. Teach vernacular, syntax, diction or rhythm without uploading writing. Edit, disable or delete lessons as preferences change.
2. **Practice:** compare two messages for a concrete situation, with a mixed comparison or a focus on vernacular, syntax, diction or rhythm. Choose A, B, or neither; rewrite a line and explain why. Presentation feedback also covers prose/bullets, paragraph length, openings, sign-offs, emoji and detail. Samples are optional.
3. **Add evidence when useful:** paste writing, import browser captures, or discover files in a chosen folder. Review suggested context and separately mark the passage as yours, AI-assisted, or not yours. Context suggestions and language lessons never confirm authorship. Nothing scans automatically.
4. **Write:** describe what you want to say and move the controls. Greater intensity changes style, never grants permission to invent facts or accomplishments.
5. **Prior knowledge:** add compact background or guidance with a source and context, then explicitly confirm it for use. It remains separate from authored samples.
6. **Evaluate:** compare a baseline and calibrated draft for the same brief, with randomized labels hidden until your vote. Choose either, tie, or neither; flag factual issues. Evaluation votes do not train the profile.
7. **Voice profile:** export a Markdown reference containing patterns, contextual feedback, active language lessons and confirmed prior packets. Raw authored examples are excluded unless you explicitly include them. Bring the reference into Codex, Cursor, or another writing workflow.

Contribution points measure participation. They are not an accuracy score. Keep some genuine writing marked as held out; it is excluded from learning evidence and prompt/export examples. A held-out partition alone is not a completed quality evaluation.

## Finding your writing

Discovery jobs save a queue and directory checkpoints in SQLite. Start with selected local folders, run bounded batches, resume after restarting, or cancel. They skip sensitive directories, hidden files, links, credential-like content and common generated trees; errors and extraction caps are reported. Changed or unreadable sources are skipped with warnings. Start a new scan to cover changed sources. A completed queue means that pass finished, not that every document was readable or the whole computer was covered.

Supported formats are TXT, Markdown, EML, DOCX and supported JSON exports, plus PDF when optional `pdftotext` is installed. Files are capped at 10 MiB, samples at 30,000 characters, conversation extraction at 500 messages per file, and PDF extraction at the first 30 pages. The older one-pass discovery API also remains available. Missing context on discovered candidates receives provisional offline suggestions; ambiguous situations can remain unknown. Explicit supplied contexts are preserved.

The [Chrome extension](extension/README.md) captures selected passages or discovers Google Docs visible in the current Drive/Docs tab. Choose up to **20 documents**; nothing is preselected. An explicit permission action enables best-effort TXT collection through the existing Google browser session. Each document gets a success or failure. Redirects, login requirements and access restrictions can prevent collection; download TXT yourself and attach it to the matching candidate as the fallback. The extension exports JSON for local import and never pushes directly to the app.

Only rendered rows/links in the current tab are discoverable. Scroll and invoke discovery again for additional visible rows. This is not exhaustive Drive discovery, account-wide sync, a Drive API integration, or a crawl of other tabs. TXT loses rich formatting. See the extension README for size, timeout, session-storage and optional-permission limits.

All discovered/imported passages start unconfirmed. A document being on your computer or in your Drive is **not** proof you wrote it. Confirm a whole passage only when it is your authored writing; for mixed-authorship documents, paste only your excerpt. AI preferences remain feedback rather than becoming authored samples.

## What “learning” means here

The current system uses context-matched examples, descriptive writing patterns, persistent positive/negative feedback, active language lessons and confirmed prior packets in generation prompts. It is **in-context calibration**, not model-weight fine-tuning. Language lessons are explicit preferences, kept separately from authored samples, prior knowledge and evaluation votes; they can teach a desired style without claiming you already write that way. Samples are optional, and presentation preferences are conditional. Your current chat's hidden state and past Codex conversations are not automatically available; supply compact prior knowledge explicitly, with its source, for review.

Context analysis uses offline heuristics or the selected Codex provider and returns a reviewable preview. Prior packets store background or guidance, never authentic writing. Blinded evaluation is a separate lane: both drafts receive the same brief and neutral controls, while only the calibrated draft receives approved examples, preferences, active contextual lessons and confirmed priors. Ratings are immutable and excluded from learning. Counts include baseline wins, calibrated wins, ties, neither and factual flags by channel; offline comparisons are counted separately as rehearsal. Preference counts do not establish achieved writing accuracy or factual correctness.

A generated draft lists the examples supplied to the model. These IDs explain retrieval, not a claim that a particular sentence came from a particular source. Strong controls are guidance to the model, not a verified factuality guarantee; review important drafts before using them.

[PRODUCT.md](docs/PRODUCT.md) separates the implemented workflow from future source grouping, fact checks, prompt optimization and weight training. [RESEARCH.md](docs/RESEARCH.md) records the source-backed research and reuse decisions.

## Local data and boundaries

- The SQLite database lives in `data/` by default; use `--data-dir /private/location` to change it. This folder is ignored by Git. The database contains private writing and is **not encrypted at rest**; use OS disk encryption and appropriate backups.
- Offline app operations stay local. **Codex generation sends selected confirmed examples, feedback, active contextual language lessons, confirmed prior packets, context and brief through your configured Codex account.** Pending, assisted, rejected and held-out samples are excluded from style evidence. Separately, explicitly requesting Codex context analysis sends the selected non-held-out candidate, even if its authorship is pending, for reviewable analysis.
- The local server binds to loopback and checks Host/Origin plus a session token for mutations. It is intended for one trusted OS user, not a shared hosted service.
- The Codex process runs in a temporary directory with a read-only sandbox, ignored user config, ephemeral mode, and major tool/integration features disabled. This narrows its behavior; it is not a new OS-level confidentiality boundary against administrator-managed configuration. The adapter does not supply arbitrary local files or source instructions as executable requests.
- Deleting a sample removes it from subsequent retrieval/profile exports. Previously downloaded exports, browser-capture files, prior generated drafts, backups and any provider-side retention are separate copies.
- Starting the app does not deploy it, start a background monitor, or ingest personal data automatically. The private source repository is separate from local writing and the database.

## Developer commands

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q voicework
node --check web/app.js
node --check extension/popup.js
node --check extension/background.js
node --test tests/test_browser_discovery.js
```

Node is optional for syntax checks, not required to run the app. See [BUILD_CONTRACT.md](docs/BUILD_CONTRACT.md) for the API and [AGENTS.md](AGENTS.md) for repository development rules.

Working name: Voicework. Naming/trademark availability has not been assessed.
