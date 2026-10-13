# Verification — 2026-10-13

## Passed

- `python3 -m unittest discover -s tests -q`: **75 tests passed**, including live HTTP integration tests using temporary loopback servers.
- `node --test tests/test_browser_discovery.js`: **11 tests passed** for document identification, selection limits, import schema, long-text splitting, Unicode boundaries, rejected content, and permission boundaries.
- Python byte-compilation and JavaScript syntax checks for the app, extension popup, service worker, and discovery module.
- Synthetic API walkthroughs: authorship review, holdout exclusion, comparison feedback, presentation controls, portable export, deletion, atomic import, context inference, reviewed prior packets, blind ratings, and resumable discovery. Host, origin, content-type, and mutation-token checks passed.
- Browser interaction: provisional context suggestions and application without changing authorship; a blind offline comparison with concealed labels; a tie rating and label reveal; rehearsal results excluded from AI results; prior packet review and confirmed-prior export. Browser console reported no errors in the final QA session.
- Desktop practice, draft, profile, and offline-limit disclosure were checked. Mobile navigation and Check fit at 390 × 844px had no horizontal overflow (`scrollWidth=390`, viewport=390).
- Persistence, duplicate handling, concurrent votes, changed-file reporting, checkpointed directory traversal, context isolation, negative feedback, and exclusion of held-out evidence are covered by tests.
- Independent Astra xhigh reviews and implementation work resolved sign-preservation, missing export contrast, provisional-context inference, resumable directory expansion, and evaluation leakage issues.

- Live `gpt-6-astra` with `xhigh` reasoning passed synthetic draft generation, two-option comparison generation, and six-field provisional context inference using the installed Codex app CLI **0.162.0-alpha.17.2**. This verifies live integration, not personal writing fidelity.
- A live browser draft submitted through the app completed with a Codex result, preserved the synthetic brief, identified the absence of style evidence, and produced no browser console errors.
- Executable selection and terminal-error classification regression tests cover `--codex-bin`, `VOICEWORK_CODEX_BIN`, and false authentication matches in catalog diagnostics.
- Sample-free language teaching: with zero samples, a direct diction lesson preferring “use” over “utilize” changed a live Astra draft from the brief “We will utilize the checklist tomorrow” to “We will use the checklist tomorrow.” This synthetic check establishes that the lesson was applied, not personal fidelity.
- Focused syntax practice generated a two-sentence option and a subordinate-clause alternative with the same facts. A chosen option, reason, and rewrite were saved and appeared in the portable profile alongside the separate diction lesson.
- Browser checks covered direct-lesson creation, editing, disabling, re-enabling, and export exclusion while disabled. Export previews are cleared when workspace data refreshes, preventing revoked lessons from remaining in a stale preview.
- Eleven new Python tests cover language-lesson persistence, validation, context isolation, revocation/deletion, zero-sample API and provider behavior, focus migration, and exclusion from the uncalibrated evaluation baseline. Practice at 390 × 844px had no horizontal overflow; the QA browser recorded no console errors.

## Not established

- **Correction to the earlier blocker diagnosis:** the PATH CLI (0.137.0) was too old for Astra. Its terminal response was HTTP 400 requesting a newer CLI; unrelated catalog text had triggered a misleading login message. The existing login works with the current bundled CLI. Voicework now reports that failure correctly and supports an explicit executable path. Global configuration and credentials were not changed.
- **Installed Chrome extension operation:** the extension is packaged and tested at the module/schema level. Live Drive markup, Google authentication/export responses, optional permissions, and installation have not been exercised in the user's browser. Redirected or blocked TXT exports have a manual download-and-attach fallback.
- **Personal writing accuracy:** no genuine personal corpus or user-rated AI benchmark was run. The blind evaluation feature is implemented; no improvement claim is established. Offline rehearsals are excluded from AI evaluation counts.
- Discovery is user-started and resumable across accessible chosen directories, with explicit exclusions, caps, and warnings. It does not prove exhaustive whole-computer coverage. Drive discovery sees rendered candidates in the current tab; it does not crawl the whole account.
- Context suggestions are provisional heuristics or optional model suggestions, not verified reconstruction of an original conversation or proof of authorship.
- Model-weight fine-tuning, automated prompt optimization, source-family holdout grouping, and automated factuality verification are not implemented. The implemented learning is contextual retrieval and saved preference feedback.
- No real personal corpus was seeded into the shipped repo or submitted to a model. QA uses synthetic data in a separate scratch directory. The user-facing app was restarted with its existing database preserved and showed an empty workspace.
- Source is published to the private repository [gavinjudd/voicework](https://github.com/gavinjudd/voicework). No hosted deployment, public release, or extension installation was created.

## Handoff

The local app is available at `http://127.0.0.1:8765` in **Codex mode**, using `gpt-6-astra` and the verified app-bundled CLI selected with `--codex-bin`. See README for the exact launch command on this Mac. The ZIP contains source and tests, excluding local databases, Git metadata, bytecode, and scratch data. The separate preview image shows the local app.

The app requires Python 3.9+ and a browser. Live integration was verified with Codex CLI 0.162.0-alpha.17.2; the older PATH CLI 0.137.0 cannot run the requested Astra model. PDF extraction requires optional `pdftotext` and is limited to the first 30 pages and 30,000 characters. The discovery UI reports extraction and traversal limits; the extension README describes browser-specific limits.
