# Contextual preference learner

This component estimates a preference from explicitly attributed evidence. It
does not train LLM parameters, infer user authorship, or treat a liked passage as
an ideal target. Existing explicit lessons remain separate and authoritative.

## Model and interpretation

A trait has a stable description, dimension and context. Evidence supplies a
signed strength from -1 (away from the defined quality) to +1 (toward it), the
reviewer's attribution and its reliability. An interpretation may remain pending;
pending and retracted records do not influence the estimate.

For each source group, contributions are averaged using source reliability and
capped at the strongest contribution in that group. The source-policy multipliers
are direct preference/edit/saved correction 1.0, correction comparison 0.9,
reference passage 0.35 and whole-profile comparison 0.25, and saved practice feedback 0.5. These are conservative
design choices, not experimentally calibrated constants.

- **Strength** is the weighted mean of the signed observations, separate from
  evidence amount. One clear strong preference can have strength 1 with little
  confidence; many consistent mild observations can have strength .2 with more
  confidence.
- **Evidence confidence** is an explicitly heuristic index:
  `mass / (mass + 3) * (1 - weighted_standard_deviation)`, where observations lie
  on [-1, 1]. It measures independent support and consistency. It is not a
  probability, a credible interval or writing accuracy.
- **Conflict** remains visible. If at least 25% of directional evidence mass
  opposes the majority, the preference is disputed and abstains from drafting.
  Retracting an interpretation recomputes the estimate while retaining it and
  its review history. A later observation never silently overwrites earlier ones. A withdrawn interpretation can be replaced with a revised value or attribution; the withdrawn record and its review history remain intact.
- **Influence** is `abs(strength) * confidence` only when the preference has
  enough evidence, is enabled, in scope and undisputed. Draft guidance is capped
  at 24 preferences and explicitly subordinate to current instructions and
  context-specific direct corrections.

All five comments from one YouTube video, including links with different `lc`
comment IDs, share one source group. A manual edit and the same edit later saved
as a correction share a group too. This catches known correlations; URL grouping
does not establish true statistical independence between different sources.

## Sources and review

`source_kind` is `direct`, `reference`, `edit`, `correction`, `quality`, `practice` or
`evaluation`. `attribution` must explain which aspect the evidence concerns.
References and model interpretations should default to pending. Explicitly
reviewed submissions can set `confirmed: true`.

Edits need different before/after text and explicit `style_only` and
`meaning_preserved` confirmations. Saved correction sources must remain active,
unchanged and style-specific. Undoing/disabling/deleting their lesson makes the
derived evidence ineligible without deleting its provenance.

Saved practice feedback additionally requires an explicit style-only and meaning-preserved confirmation; offline and neither choices are ineligible. A comparison must already be rated and use a real provider. Both options in a
correction comparison must preserve meaning. Whole-profile comparisons flagged
for factual differences, offline rehearsals, ties and neither judgments cannot
supply directional style evidence. A vote alone never trains this learner;
explicit aspect attribution is a separate action and does not alter the vote.

Held-out samples cannot supply reference evidence. Manual passages are checked
for known exact held-out copies and substantial contiguous excerpts too. This
does not detect paraphrased contamination. Raw passages stay in the local
evidence records; `resolve()` and `guidance()` return no source passages.

## Context and persistence

Traits and evidence use the existing conservative `lesson_context_matches`
policy, including the established one-way broad long-form scope. Evidence can
narrow a trait but cannot silently broaden it or the saved source's conditions.
A broad trait's default estimate excludes narrower evidence until the requested
context satisfies that evidence. The UI reports this as out-of-scope evidence and
offers a situation preview. An explicit malformed or empty context is rejected rather than silently defaulted.

The three new SQLite tables are `preference_traits`, `preference_evidence` and
`preference_evidence_history`. Existing samples, lessons, feedback, priors,
corrections and evaluation tables are not rewritten. Source identity deduplicates
double-clicks; evidence and its history are transactional. Trait meaning and
context cannot be edited under existing evidence; create a separately scoped
trait when those change. Labels and enabled state remain editable.

## Python interface

```python
learner = PreferenceLearner(sqlite_path)
trait = learner.create_trait({"label": "Concrete explanations",
    "description": "Explain a mechanism with concrete details when useful.",
    "dimension": "diction", "context": {"channel": "long-form writing"}})
event = learner.add_evidence(trait["id"], {"source_kind": "reference",
    "reference_text": "A synthetic reference passage.",
    "source_url": "https://example.test/post", "value": .5,
    "reliability": .5, "attribution": "I like the concrete mechanism.",
    "confirmed": False})
learner.review_evidence(event["id"], "confirm", "Reviewed the specific aspect")
learner.resolve({"channel": "technical article"})
learner.guidance({"channel": "technical article"})
```

`get_trait(id, context=None, include_evidence=True)` estimates from all applicable
evidence, while returning at most 50 raw evidence rows and 100 recent review
events. `list_evidence(id, limit=50, offset=0)` provides bounded pagination.
`list_traits(context=None, include_evidence=False)` is suitable for app state.
`resolve(context, exclude_source_ids=())` can exclude a source in an isolated
correction check; `guidance` returns proportional supporting instructions with
strength/confidence metadata and linked legacy lesson IDs.

## HTTP adapter and UI

`PreferenceRoutes(learner).route(handler, path, query, body)` supplies:

- `GET/POST /api/preferences`, `GET/PATCH /api/preferences/:id`.
- `POST /api/preferences/resolve {context}`.
- `GET /api/preferences/:id/evidence?limit=50&offset=0`.
- `POST /api/preferences/:id/evidence`.
- `POST /api/preference-evidence/:id/confirm|retract|restore {reason}`.

The host must run its existing same-origin/token checks before dispatch.
`web/preference-learner.mjs` exports `renderPreferenceLearner` and
`seedPreferenceEvidence`. It expects existing `state`, `api`, `refresh`, `toast`
and `contextEditor` helpers. App state should include `preference_traits` from
`list_traits()`. Load `web/preference-learner.css`. Edit or rated-comparison UI
actions can seed the evidence form without automatically saving an attribution.

## Verification scope

`tests/test_preference_learner.py` covers separate strength/confidence,
same-source caps, contradictory/retracted evidence, source eligibility,
idempotency under concurrent writes, additive schema, held-out exclusions,
context boundaries, raw-passage exclusion, atomic failures, pagination and
source deactivation. These tests establish implementation behavior; whether the
learned preferences improve an author's drafts still needs that author's
judgment on fresh outputs.

## Integrated behavior

`writing_inputs(..., learner=...)` is shared by message/document drafting, practice, evaluation and MCP. Provider payloads separate `learned_preferences` from calibration feedback and authored examples. Whole-document review sees the exact frozen estimates used to generate the text. Correction checks exclude evidence from their target correction and withhold any remaining trait linked to the target lesson to keep the contrast isolated.

The profile panel, editor actions and rated-comparison actions lead to explicit attribution review. Copying or editing alone teaches nothing. Readiness counts applicable learned preferences as saved guidance only; no preference or comparison creates authorship evidence. HTTP and MCP exports contain scoped estimates and no raw preference passages. Story Engine reserves at most four places for supporting learned preferences while retaining its measured-habit reserve. MCP can propose evidence for an existing trait, always pending, and cannot confirm it.

The thresholds (`abs(strength) >= .1`, confidence >= .15, opposing directional mass >= .25 for disputes), source multipliers, influence ranking and four Story Engine slots are product policies, not fitted or validated statistical parameters. Known source groups are correlated batches, not a claim that all remaining groups are statistically independent. Fresh writing quality still needs the author's judgment.

## Learning qualities rather than freezing examples

A trait description states the contextual quality to favor or avoid. Its attached edit/reference stores the evidence and attribution separately; the raw words do not enter generation guidance. An example can support expressive degree, personal reaction or conversational cadence without authorizing the same phrase everywhere. Guidance calls for fresh expression that preserves the reviewed quality, and forbids manufacturing intensifiers, feelings or facts. New interpretations stay pending unless explicitly reviewed; existing confirm/withdraw/revise/pause controls retain their history. Saving a reference never declares user authorship.

When a direct instruction and its illustrative edit describe the same observation, give the direct evidence the edit's group key (`_hash([before_text, after_text])` as `batch_key`) so they share the existing capped source group. They are two provenance views of one observation, not independent confirmations. Reopening the SQLite-backed learner recomputes the same estimate. Generalization is scoped guidance, not a validated prediction of taste; a wider context or psychological explanation remains a separate reviewable hypothesis.
