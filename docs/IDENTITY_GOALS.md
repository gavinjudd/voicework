# Identity and goals, grounded in context

The layer models a user's chosen communication outcome and presence for a particular situation. It stores four editable fields: `goal`, `presence`, `long_term_goals` and `boundaries`. Style habits and explicit lessons remain separate and retain priority, alongside current instructions and factual fidelity. A wish to seem capable cannot authorize invented experience or force a formal register.

## Collection and interpretation

The external assistant is a collector. It can use tools and context already available and authorized in its client, then submit selected original passages with a title, source reference, date when known, collector and source group. `source_nature` distinguishes `original`, `assistant_summary` and `unknown`; these labels are supplied metadata, not verified provenance. Contexts require an explicit channel and can constrain audience, intent, relationship and stakes. Voicework does not crawl connectors or whole conversation archives.

The app's configured provider performs identity inference as an explicit asynchronous job. Its prompt treats all sources as untrusted and does not provide tools, connector access or hidden desktop memory. A cheap collector need not write structured identity proposals. A collector can still omit, distort or invent context; exact-quote validation proves only that a quotation occurs in the submitted material. Human inspection of source/provenance remains available.

Plain context and feedback accept 24,000 characters; edits accept 12,000 characters per side. One pass accepts one to twelve sources and 96,000 serialized payload characters. Profiles retain at most 200 sources. Oversized or ineligible batches fail visibly without truncation. Credential-like inputs, credential-bearing links and held-out writing are refused. Source bodies remain in the private local database and are sent to the configured model only on explicit processing. App source and exports contain no seeded personal archives.

Native references, style corrections, practice feedback, rated quality checks, rated whole-profile comparisons and prior packets reuse existing records. Context cannot broaden a native record's scope. Offline/unrated/factually flagged comparison evidence and withdrawn or changed native sources are ineligible. Third-party references and unconfirmed assistant priors remain labeled as such; they never become authored samples or confirmed facts.

## Review and application

Model results require exact source quotes and rationale for each changed field. Unknown goals may stay empty. Uncertainties, conflicts, questions and no-change analyses persist across reloads. The app records which provider produced the analysis; repeated identical inputs reuse that analysis. Conflicting or insufficient evidence can produce abstention.

An empty intake applies nothing. Inferred updates stay pending until a user reviews them. Review supports field selection, revised wording, rejection and undo. Profile revisions prevent a stale tab or late model result from overwriting newer work. Undo is allowed only while its applied revision is current. Direct user-entered values are explicit choices and do not require AI inference.

Approved fields reach the common writing compiler used by drafting, document rewriting/review, practice, blind comparisons, MCP voice guidance and the Markdown profile export. Raw identity intake passages are excluded from those outputs. Context matching chooses the most specific applicable profile; incompatible equally specific profiles abstain with a warning. Pausing a profile or withdrawing a cited source stops affected derived guidance. Isolated correction checks exclude identity derived from the tested correction, including equivalent submitted edit pairs. Whole-document jobs freeze their selected profile for the job's duration. The separate legacy Story Engine voice-file format does not carry this identity layer.

This is reviewable in-context calibration. It does not fine-tune model weights, prove factual identity, diagnose personality, measure social capital, optimize a user's life autonomously or certify that a rewrite sounds like them.

## Collector handoff

Use the existing local MCP server with the same data directory as the app:

1. `get_identity_intake` to find a context, or `create_identity_intake` with an explicit context and label.
2. `submit_identity_context` with `profile_id`, `kind`, source text or a native `source_id`, provenance and `source_nature`. Submit original text rather than a proposed persona. Preserve ambiguity, contradictions and the limits of the available context.
3. `process_identity_context` with selected source IDs. The same-profile app performs the inference with its configured provider.
4. `get_identity_learning_job` to read completion. Show pending results under Voice profile for the user's review.

There is no MCP identity-acceptance tool. The process endpoint checks the running app's profile-instance digest and refuses a different data directory. The transport is loopback-only and reuses the app's origin/token protections. The app must be running with a live provider for processing; context collection and guidance can work without it. Local stdio-capable clients can register this server; no new client configuration or connector permissions are installed automatically. Other assistants can supply a selected text file for the user to open in Voicework.

The HTTP surface is `GET/POST /api/identities`, `GET/PATCH /api/identities/:id`, `GET/POST /api/identities/:id/sources`, `GET /api/identities/:id/intake-plan`, `POST /api/identities/:id/infer`, `GET /api/identity-jobs/:id`, `POST /api/identity-proposals/:id` (accept/reject/undo), `PATCH /api/identity-sources/:id` (withdraw/restore), and `POST /api/identities/resolve`. These add four tables: `identity_profiles`, `identity_sources`, `identity_proposals`, `identity_history`; existing authored, lesson, feedback and preference rows are preserved.
