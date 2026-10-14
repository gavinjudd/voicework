# Voicework

A local writing practice studio that learns how you write **for a particular audience, situation, and purpose**—including how you present it.

Start without samples: write a useful draft, edit it your way, and choose which corrections to remember. Preview the takeaway and its context before saving it; undo a remembered correction when it stops helping. Focused practice and direct language lessons remain available. Optionally add reviewed writing to ground the profile in authentic examples. Personal texting and professional writing retain separate contextual evidence.

**Whole documents:** open Write → Whole document to paste Markdown/plain text or open a local `.md`/`.txt` file. Select the trained context that fits, adjust audience/purpose and register, then rewrite. The engine processes bounded sections with a frozen voice profile, preserves protected document structure, and returns editable text with separate content and voice checks. Editing makes previous checks stale; recheck the revised text before relying on them. The source and generated writing do not become training samples.

The document limit is **120,000 characters**; output is capped at **240,000 characters**. Rich Word/PDF layout and arbitrary MDX components are not supported as editable document formats. Model review is an additional diagnostic, not proof of fidelity or factual correctness. Offline mode preserves the source exactly and marks semantic/voice review unavailable.

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

1. **Write and edit:** describe a real situation, generate a draft, and edit the result directly. Copy uses the edited text. Edits and intensity controls affect this draft only; they do not automatically train the profile.
2. **Remember a useful correction:** review the before/after wording and a plain-language takeaway. Save it for similar situations with a specific audience, intent, relationship or stakes, or deliberately broaden it to the whole channel. Meaning/fact corrections stay with the draft. Remembered corrections remain preferences, not authored samples.
3. **Check the change:** use a fresh brief to compare drafts with and without one remembered correction, holding the other current guidance constant. Choose which sounds more like you, then separately assess meaning and editing effort for each option. Labels are hidden until you rate. Results never train the profile or automatically activate or undo a rule.
4. **Undo when needed:** correction history retains the original change and a persistent undo action. Undo disables that correction's unchanged lesson, preserving unrelated lessons. If the lesson has since been edited, review it in the profile instead of overwriting the newer work.
5. **Practice or teach directly:** focused comparisons and language lessons support vernacular, syntax, diction, rhythm, and presentation without samples. They remain useful when you want to work on a particular distinction.
6. **Add evidence when useful:** paste writing, import browser captures, or discover files in a chosen folder. Review suggested context and separately confirm authorship. Compact prior-knowledge packets also require explicit review. Nothing scans automatically.
7. **Export your profile:** take contextual preferences, active lessons and confirmed priors into another writing workflow. Raw authored examples are excluded unless you explicitly include them.

Contribution points measure participation. They are not an accuracy score. Keep some genuine writing marked as held out; it is excluded from learning evidence and prompt/export examples. A held-out partition alone is not a completed quality evaluation.

Use **long-form writing** for a lesson you deliberately want applied across nonfiction articles, essays, blog posts, and project case studies. These general lessons also reach technical-article drafts; narrower lessons still retain their audience, purpose, relationship, and stakes conditions. Specific preferences and the draft's explicit register take precedence. This one-way scope applies only to lessons: it never mixes authored samples, feedback, or background facts between formats. Messages, emails, fiction, academic/legal formats, and unknown or qualified channel labels remain separate. Approved AI-assisted article edits should be remembered as corrections, not relabeled as authored samples.

The **Voice confidence** bar in Practice, Samples, and Voice profile shows three evidence-readiness checkpoints: save authored evidence or direct teaching, rate a real-model blind voice comparison, and rate at least three distinct brief/context pairs across at least two known channels. These checkpoints describe what has been checked, not a probability that a draft sounds like you. The details show wins, losses, ties, neither votes, and factual flags separately. Offline rehearsal and correction-specific checks do not advance whole-voice comparison checkpoints. Historical comparisons also do not validate later changes to your profile. Use **Check fit** in the confidence panel to open the whole-profile comparison.

## Finding your writing

Discovery jobs save a queue and directory checkpoints in SQLite. Start with selected local folders, run bounded batches, resume after restarting, or cancel. They skip sensitive directories, hidden files, links, credential-like content and common generated trees; errors and extraction caps are reported. Changed or unreadable sources are skipped with warnings. Start a new scan to cover changed sources. A completed queue means that pass finished, not that every document was readable or the whole computer was covered.

Supported formats are TXT, Markdown, EML, DOCX and supported JSON exports, plus PDF when optional `pdftotext` is installed. Files are capped at 10 MiB, samples at 30,000 characters, conversation extraction at 500 messages per file, and PDF extraction at the first 30 pages. The older one-pass discovery API also remains available. Missing context on discovered candidates receives provisional offline suggestions; ambiguous situations can remain unknown. Explicit supplied contexts are preserved.

The [Chrome extension](extension/README.md) captures selected passages or discovers Google Docs visible in the current Drive/Docs tab. Choose up to **20 documents**; nothing is preselected. An explicit permission action enables best-effort TXT collection through the existing Google browser session. Each document gets a success or failure. Redirects, login requirements and access restrictions can prevent collection; download TXT yourself and attach it to the matching candidate as the fallback. The extension exports JSON for local import and never pushes directly to the app.

Only rendered rows/links in the current tab are discoverable. Scroll and invoke discovery again for additional visible rows. This is not exhaustive Drive discovery, account-wide sync, a Drive API integration, or a crawl of other tabs. TXT loses rich formatting. See the extension README for size, timeout, session-storage and optional-permission limits.

All discovered/imported passages start unconfirmed. A document being on your computer or in your Drive is **not** proof you wrote it. Confirm a whole passage only when it is your authored writing; for mixed-authorship documents, paste only your excerpt. AI preferences remain feedback rather than becoming authored samples.

## What “learning” means here

The current system uses context-matched examples, descriptive writing patterns, persistent positive/negative feedback, active language lessons and confirmed prior packets in generation prompts. It is **in-context calibration**, not model-weight fine-tuning. Language lessons are explicit preferences, kept separately from authored samples, prior knowledge and evaluation votes; they can teach a desired style without claiming you already write that way. Samples are optional, and presentation preferences are conditional. Your current chat's hidden state and past Codex conversations are not automatically available; supply compact prior knowledge explicitly, with its source, for review.

Context matching recognizes a small, explicit vocabulary of equivalent labels, such as “request review” and “ask for feedback,” while preserving every populated lesson condition. Audience, relationship, stakes and channel boundaries keep incompatible examples out. Specific requests need relevant evidence; the app no longer fills the prompt with unrelated same-channel samples. Custom and qualified labels stay literal, and scenario notes remain conditional guidance. This is conservative matching, not general semantic understanding.

Technical-article labels now share an explicit family, and equivalent technical-explanation intents match within that family. The routing report shows aliases, selected evidence/lesson counts and missing guidance; it does not silently infer a new audience or rewrite stored contexts. Formality can be set explicitly with `register: auto|casual|professional|formal`.

## Use it from another AI or script

Keep the local app running with your chosen provider. Create a JSON request containing `source`, `context`, and optional `controls`, then run:

```sh
python3 -m voicework.harness --request request.json --output result.json
```

```json
{
  "source": "# Investigation\n\nThe cache handled 17 requests. It did not reduce storage.",
  "context": {
    "channel": "technical article",
    "audience": "technical readers",
    "intent": "explain technical research"
  },
  "controls": {"register": "professional", "structure": "auto"}
}
```

To check an edited result without rewriting, include `text` alongside the original `source` and add `--check`. The JSON result contains the draft, separate checks, source/draft hashes, and context selection provenance. Exit code **0** means checks completed with no reported issue, **3** means review is required, and **2** means the operation failed. None of these codes certify personal voice accuracy. Output files must be new and are created with private filesystem permissions. Use `--request -` for stdin; omitting `--output` deliberately writes the result to stdout.

The same interface is available through `POST /api/documents`, `POST /api/document-checks`, and polling `GET /api/documents/:id`. Jobs run in memory, survive browser refresh, and disappear on server restart; copy/download results you need to keep. Completed jobs expire after one hour or when evicted from the bounded result cache. See [BUILD_CONTRACT.md](docs/BUILD_CONTRACT.md).

Context analysis uses offline heuristics or the selected Codex provider and returns a reviewable preview. Prior packets store background or guidance, never authentic writing. Blinded evaluation is a separate lane: both drafts receive the same brief and neutral controls, while only the calibrated draft receives approved examples, preferences, active contextual lessons and confirmed priors. Ratings are immutable and excluded from learning. Counts include baseline wins, calibrated wins, ties, neither and factual flags by channel; offline comparisons are counted separately as rehearsal. Preference counts do not establish achieved writing accuracy or factual correctness.

Correction quality checks answer a narrower question: does this particular saved correction help? Both variants receive the same current evidence and other preferences; only the selected correction differs. Generation order and display labels are randomized. The saved case records the model, controls and fingerprints of the fixed inputs. This is not a replay of an entire historical profile. Voice choice, meaning judgments and expected editing effort remain separate, and offline checks never count as real-model results. Use multiple fresh situations before drawing a conclusion; one favorable comparison does not establish improvement.

A generated draft lists the examples supplied to the model. These IDs explain retrieval, not a claim that a particular sentence came from a particular source. Strong controls are guidance to the model, not a verified factuality guarantee; review important drafts before using them.

[PRODUCT.md](docs/PRODUCT.md) separates the implemented workflow from future source grouping, independent fact verification, prompt optimization and weight training. [RESEARCH.md](docs/RESEARCH.md) records the source-backed research and reuse decisions.

## Tell stories in your voice (Story Engine)

**Voice profile → Use with Story Engine** turns your profile into a voice file for [Story Engine](https://github.com/gavinjudd/story-engine). Describe the story use (for example *spoken interview answer*) and tick the channels whose habits should carry over. Channels saved as broad long-form lessons are preselected. The others stay off until you choose them. Preview, then export `story-voice.json`. The same is available offline from the command line, reading the local database directly:

```sh
python3 -m voicework.story_bridge --list
python3 -m voicework.story_bridge --target 'spoken interview answer' --channel 'long-form writing' \
  --output story-voice.json            # add --examples to include up to 5 confirmed excerpts
python3 story_engine.py bundle --voice-profile story-voice.json --seed '…'   # in the story-engine repo
```

Enabled lessons, including remembered corrections, become traits and avoidances. Practice reasons and story-relevant presentation preferences come next, followed by cadence measured from confirmed, non-held-out samples in the chosen channels. Every trait keeps its learned context, such as `[work email · audience: teammates]`, and broader lessons outrank narrowly conditioned ones. The output is capped at 32 traits and 24 avoidances, and the file reports what was trimmed. Held-out or unconfirmed samples, AI and edited practice options, prior packets (which may contain facts), emoji and sign-off preferences, and evaluation ratings are never included. Excerpts require explicit opt-in. With no usable guidance, `voice_profile` is `null`. The file describes reviewed style evidence. It does not measure fidelity or prove authorship. CLI output files are new and created with mode 0600.

## Local data and boundaries

- The SQLite database lives in `data/` by default; use `--data-dir /private/location` to change it. This folder is ignored by Git. The database contains private writing and is **not encrypted at rest**; use OS disk encryption and appropriate backups.
- The current draft, edits, and pending correction preview are also saved in this browser's local storage so navigation and refresh do not lose them. They remain separate from saved lessons and the SQLite database.
- Offline app operations stay local. **Codex generation sends selected confirmed examples, feedback, active contextual language lessons, confirmed prior packets, context and brief through your configured Codex account.** Pending, assisted, rejected and held-out samples are excluded from style evidence. Separately, explicitly requesting Codex context analysis sends the selected non-held-out candidate, even if its authorship is pending, for reviewable analysis.
- The local server binds to loopback and checks Host/Origin plus a session token for mutations. It is intended for one trusted OS user, not a shared hosted service.
- The Codex process runs in a temporary directory with a read-only sandbox, ignored user config, ephemeral mode, and major tool/integration features disabled. This narrows its behavior; it is not a new OS-level confidentiality boundary against administrator-managed configuration. The adapter does not supply arbitrary local files or source instructions as executable requests.
- Deleting a sample removes it from subsequent retrieval/profile exports. Previously downloaded exports, browser-capture files, prior generated drafts, backups and any provider-side retention are separate copies.
- Explicitly remembering a correction stores its original and revised text locally as provenance. Undo stops its lesson from guiding future drafts; it does not erase that history or previously generated quality-check outputs. Correction source drafts are not automatically included in exported writing examples.
- Starting the app does not deploy it, start a background monitor, or ingest personal data automatically. The private source repository is separate from local writing and the database.

## Developer commands

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q voicework
node --check web/app.js
node --check extension/popup.js
node --check extension/background.js
node --test tests/test_browser_discovery.js tests/test_correction_diff.js tests/test_voice_confidence.js tests/test_document_state.js
```

Node is optional for syntax checks, not required to run the app. See [BUILD_CONTRACT.md](docs/BUILD_CONTRACT.md) for the API and [AGENTS.md](AGENTS.md) for repository development rules.

Working name: Voicework. Naming/trademark availability has not been assessed.
