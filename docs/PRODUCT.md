# Voicework product and architecture

Current implementation guide. Read [RESEARCH.md](RESEARCH.md) for external evidence, [BUILD_CONTRACT.md](BUILD_CONTRACT.md) for the API, and [VERIFICATION.md](VERIFICATION.md) for observed checks. Future work is explicitly separated.

## Product promise

Help a person get useful drafts that sound appropriate for them **in this situation**, and carry their writing preferences between tools. Start with direct teaching and small comparisons; authentic writing samples are optional. Learn through explicit rules, preferred and avoided phrasing, edits, and context labels. Show the evidence behind the profile and what remains unknown.

“Write in my voice” alone is an established feature. The product hypothesis is that **reviewed authorship, situational preferences, presentation choices, and an inspectable portable profile** make personalization more useful and trustworthy. This hypothesis needs user evaluation; the prototype does not demonstrate market demand or writing fidelity.

## The first useful loop

The default entry is writing for a real task. A generated draft can be edited directly, copied with those edits, or restored. Editing and changing intensity are temporary operations. The user explicitly chooses **Remember this correction**, reviews the exact change and takeaway, and selects where it applies. Facts/meaning corrections remain local to the draft and cannot be saved through the style-correction API.

The narrow scope requires an audience, intent, relationship or stakes condition; channel-wide application is a separate explicit choice. Scope is previewed before saving. The correction and its language lesson are saved together, with original/revised text kept as local provenance. A persistent undo action disables only the linked unchanged lesson, leaving other teaching intact. If the linked lesson was edited later, undo must report the conflict instead of discarding those newer edits. No correction implies genuine authorship, and generated text never enters the sample collection automatically.

Practice remains available for deliberate training:

1. **Teach a small distinction.** Add a context-specific rule and preferred or avoided wording, or start a comparison immediately. No writing sample is required. Direct lessons can be edited, disabled or deleted.
2. **Practice for a real situation.** Give two short drafts for the same context. Use a mixed comparison or focus on vernacular, syntax, diction or rhythm. Choose A, B, or neither; optionally edit and say why. Ask about one concrete distinction rather than demanding a full style manifesto.
3. **Add authentic evidence when useful.** Paste a sample, scan a selected local folder, or import a browser passage. Everything enters review. Label it `confirmed`, `assisted`, `rejected`, or leave it `pending`, and review channel, audience, intent, stakes, relationship and scenario. A document can contain quotes, templates, other people's words and AI assistance.
4. **See what changed.** Retain lessons separately from comparison choices, both alternatives, edits, reasons and presentation settings. Show contribution counts and contexts covered. A lesson or selected output establishes a preference, not an authentic writing sample or authorship claim.
5. **Use and export it.** Generate a draft from a factual brief, inspect evidence references, adjust presentation, and export a compact profile for another assistant. Raw examples are opt-in.

A practical first session is one direct lesson and a few short rounds in one context, with reviewed samples added if available. That is a usability target, not a scientific minimum or a claim that the app has learned the whole person. Allow stopping, skipping, and “neither.” XP rewards contributions; it must never appear as an accuracy, confidence, or personality score.

## What is being learned

Keep three concepts separate:

| Layer | Examples | Authority |
| --- | --- | --- |
| Observed writing | Sentence lengths, paragraph breaks, openings, punctuation | Descriptive patterns from confirmed samples; affected by source and context |
| Stated or demonstrated preference | A direct language lesson; “No greeting for an ongoing teammate chat”; prefers B to A | User teaching or feedback, scoped to its situation; may be aspirational rather than habitual |
| Facts for this draft | Dates, names, numbers, achievements, commitments, uncertainty | The current factual brief; samples are style references, not authority to import old facts |

Context is multidimensional. A hiring-manager email and an internal incident update can both be “work” but require different pacing, directness, structure, and warmth. `context.py` now returns reviewable suggestions for all six fields, with rationale, uncertainty and authorship cautions. Offline heuristics or Codex analyze the selected candidate. Applying a preview edits context only; authorship confirmation remains separate. Held-out samples cannot be sent for this analysis. Discovered candidates without supplied context receive provisional offline suggestions; ambiguous fields remain unknown.

Presentation is part of voice: paragraphs versus bullets, heading frequency, paragraph length, openings and salutations, signoffs, emoji, and level of detail. Record explicit preferences for these dimensions beside wording preferences. Do not infer a permanent global rule from a single short exchange. Conflicting examples may reflect different situations or changing preferences.

Language lessons make wording teachable directly: **vernacular** covers familiar expressions and register; **syntax** covers sentence construction; **diction** covers word choice; **rhythm** covers pacing and variation in sentence length. Each lesson stores a context, dimension, rule and preferred/avoided phrasing. Enabled matching lessons enter calibrated prompts; exports include all enabled lessons with their declared contexts. They remain distinct from authentic samples, background priors and blinded evaluation records. Focused practice asks the model to vary a selected dimension while preserving meaning; it does not certify that all other dimensions stayed identical.

Lesson matching requires the same channel and whole-label matches for every audience, intent, relationship or stakes condition declared on the lesson. Case/whitespace normalization and a finite field-specific alias vocabulary recognize equivalent wording; custom labels and qualifiers are preserved. Specific requests retrieve only relevant examples and practice feedback, with incompatible audience, relationship and stakes excluded. Leave those optional conditions blank for a broader rule. Scenario text remains conditional guidance for the model rather than an enforced retrieval filter.

## Prior knowledge and authentic writing

The app has no automatic access to a Codex desktop chat's hidden memory, other conversations, a different PC's filesystem, or the user's whole Google Drive. Running a new CLI instance does not transfer that context. The adapter receives explicitly supplied evidence, feedback, active contextual language lessons and confirmed prior packets. The optional Chrome collector uses the existing browser session only for selected documents after permission.

Prior knowledge is a first-class record: a compact packet with label, text, source and context, initially unconfirmed. An assistant can prepare one from information explicitly available to its session; raw JSON conversation archives are rejected in this lane. The user reviews and confirms it before contextual use, or deletes and replaces it to correct the text. Confirmed packets appear separately in generation prompts and portable exports. Confirmation does not independently verify their claims or establish authorship. The app starts without personal samples or priors.

For PC Downloads, run the app on that PC or transfer selected files through an explicit export. A local folder scanner can read only paths available to the machine running it. A file being in Downloads, owned by the user, or containing their name does not establish authorship. For shared Google Docs, ownership and editor metadata likewise do not identify the author of each passage.

## This build's scope

| Capability | MVP boundary |
| --- | --- |
| Local data | Python standard library HTTP server, SQLite, dependency-free browser interface; loopback by default |
| Discovery | Resumable user-started jobs with persistent queues/checkpoints, bounded batches, source-change warnings and cancellation; paste/import and older one-pass scan remain available |
| Review | Authorship status, source information, reviewable context inference with reasons/uncertainty, editable context, deletion, holdout flag |
| Prior knowledge | Separate review/confirmation/deletion of compact context packets; confirmed packets used only in matching channels and exported as prior context |
| Direct teaching | Context-specific language lessons, rules and preferred/avoided phrasing; edit, disable or delete; no samples required |
| Practice | Mixed or focused vernacular/syntax/diction/rhythm comparisons; pairwise options, neither, edits, reasons, structured presentation feedback |
| Learning | Descriptive pattern aggregation and contextual feedback supplied in prompts; no model weight updates |
| Generation | Deterministic offline rehearsal by default; optional configured Codex CLI adapter; failures must not silently change provider |
| Browser bridge | Passage capture plus currently visible Drive/Docs candidates; choose up to 20 for optional authenticated TXT collection, explicit failures and manual TXT attachment fallback; JSON import, no localhost push |
| Portability | Markdown voice profile with active lessons and confirmed priors; confirmed non-held-out examples only when explicitly requested |
| Evaluation | Blinded baseline/calibrated drafts for identical briefs/controls, randomized labels, immutable votes, factual flags, per-channel counts; offline rehearsal excluded from real-model counts |

Offline mode exercises the workflow with heuristics and fixed focused contrasts. It stores direct lessons but does not semantically apply them or run an AI voice model. Codex mode requires a compatible local CLI and sends the supplied prompt through that provider. Local storage does not mean cloud-backed generation stays on the device. A synthetic live Astra/`xhigh` draft passed with the app-bundled CLI 0.162.0-alpha.17.2; PATH's older 0.137.0 failed model compatibility, not authentication. This establishes working generation for that check, not personalized writing accuracy. The executable can be selected with `--codex-bin` or `VOICEWORK_CODEX_BIN`; see the [README](../README.md). The extension has not been verified installed in the user's Chrome profile.

Discovery covers supported readable material in selected local roots. Changed directories require a new scan for their changed remainder; completion does not erase skipped-file warnings. Chrome discovery covers rendered rows/links in one tab, not all Drive files. Direct document collection is a best-effort browser-session download bridge, not Drive OAuth/API access; sign-in, redirects, sharing rules and blocked downloads can require manual TXT attachment. A TXT import cannot preserve rich formatting or verify the attached file's source association.

## Architecture and boundaries

```text
Manual input / selected local files / browser JSON
                    |
                    v
             Pending samples
                    |
       User review + context + holdout
                    |
           SQLite evidence store
                    |
       Confirmed non-held-out samples
   + feedback + active language lessons
             + confirmed priors
                    |
        Context selection + prompt
                    |
          Offline or Codex provider
                    |
         Draft / pairwise practice
                    |
      Preference record / profile export
```

Direct language lessons and sample-free practice can enter the prompt/profile path without the sample-review branch. Context-analysis previews feed the user review step. Evaluation uses an independent table and compares generation with and without approved evidence and contextual teaching; its votes never enter practice feedback or authored samples. Source is published in the private [gavinjudd/voicework repository](https://github.com/gavinjudd/voicework); local user data remains separate.

Imported text is untrusted evidence, including any instructions embedded in it. It must not become application instructions or trigger actions. Keep raw source text distinct from profile assertions and generated outputs. Excluding or deleting a sample must remove it from subsequent retrieval/export; previously downloaded files and provider copies cannot be retroactively erased by deleting the local record.

Future schema additions should include a source-document group ID, source timestamp, capture/extraction method, authorship attestation, span offsets for quoted/mixed material, context provenance, profile version, and model/prompt version. These fields are not all present in the MVP. They enable deduplication, correction, traceability, and fair evaluation; a fingerprint alone only detects a limited class of duplicates.

## Controls: presentation changes, stable facts

The current controls are polish, detail, warmth, assertiveness, and structure. Interpret low-to-high “inflation” as **more elaboration or stronger presentation of supported information**, not inflated accomplishments or invented specificity. An assertive version cannot turn “I think” into “I verified,” “helped” into “led,” or a plan into a completed result. More warmth cannot introduce an apology, promise, or concession absent from the brief.

These are generation instructions, not enforced factual guarantees. Review remains necessary. The next hardening step is a fact ledger extracted from the brief, then a diff that flags changed numbers, entities, commitments, certainty, and attribution before the result is accepted. The ledger, verifier, and blocking policy are not implemented in this build.

## Evaluation before bigger training

Correction quality checks isolate one remembered correction. Generate two drafts for a fresh brief in an applicable context, with the same current eligible samples, feedback, priors, other lessons, model and neutral controls. Only the target lesson differs. Randomize generation order and A/B presentation independently; withhold mapping and candidate warnings until rating. Record voice choice (A, B, tie or neither), meaning (preserved, changed or unsure for each candidate), and expected editing effort (none, light or substantial for each). These are user judgments, not automatic factual verification or measured editing time. Failed partial generations create no case. Provenance freezes identifiers and input fingerprints without retaining additional raw sample snapshots or reviving excluded material.

Checks can be repeated on fresh situations, and a poor result can lead the user to undo the correction. Ratings never automatically change teaching. This comparison uses the current profile with and without a selected saved correction; a complete historical profile-version manager is not implemented. Checks for inactive or subsequently edited corrections are rejected so that the label cannot silently refer to a different lesson.

The separate baseline evaluation remains available. It creates uncalibrated and calibrated drafts using the same scenario, factual brief, provider and neutral controls. Only the calibrated draft receives approved examples, preferences, active contextual language lessons and confirmed priors. Display order is randomized; mapping and candidate-specific warnings stay hidden until the immutable rating. Choices are A, B, tie or neither, with a factual-issue flag and notes. All evaluation records stay separate from training. Codex ratings contribute raw preference counts; offline ratings are rehearsal only. A preference win and a meaning problem may coexist, so counts never certify correctness.

Hold out whole source documents and their versions, threads, and near-duplicates before computing profiles or optimizing prompts. A held-out sample must not appear as an example, profile statistic, feedback-derived rule, or optimizer input. Manually reserving one sample does not automatically establish those broader isolation guarantees. Use a fixed test set for final comparison, separate from development/validation. Keep in-context performance and transfer to a new context separate.

Begin with a small balanced panel of real scenarios, record its size and per-context outcomes, then repeat after a defined feedback batch. A tiny panel gives directional feedback, not a reliable general accuracy percentage. Automated style similarity and word overlap are diagnostics; the user's preference under factual constraints is the target.

## Roadmap with exit criteria

1. **Make the manual loop useful.** Validate review, practice, draft, restart persistence, deletion, holdout exclusion, and export with real user-selected material. Fix capture and context friction before widening ingestion.
2. **Improve evidence and evaluation.** Build on isolated correction checks and subjective editing-effort ratings with source grouping, full profile versions, automatic fact checks and observed editing-time measurements. Proceed only when the user can correct a bad rule and see that correction in later drafts.
3. **Optimize prompts experimentally.** Compare contextual retrieval and a frozen hand-written profile against DSPy or TextGrad on development data; promote only with held-out benefit and no factual regression.
4. **Extend source integrations when needed.** Add a supported selected-file Drive API connector and source refresh if the current Chrome selection/TXT path proves insufficient. Add richer formatting extraction where plain text loses useful evidence.
5. **Consider actual model training.** Export consented, versioned preference pairs and approved edits; compare SFT/DPO against the best prompt baseline on separate test data. Confirm provider availability, training costs, base-model rights, deletion implications, and rollback before starting jobs. No fixed sample-count promise.

The immediate success criterion is a repeatable, trustworthy loop that reduces the user's editing effort in one context. Broader context coverage, integrations, and training should earn their complexity through measured benefit.
