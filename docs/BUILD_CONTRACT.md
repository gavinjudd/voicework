# Voicework implementation contract
Local-first personal writing calibration app. Python 3.9+ stdlib HTTP/SQLite backend; dependency-free ES module browser UI. Run python3 -m voicework at 127.0.0.1:8765. Default provider=offline (explicitly labeled heuristic rehearsal), optional provider=codex via local CLI configured by operator. No remote publishing. No real personal samples seeded.

## Layout
- `voicework/server.py`, `store.py`, `learning.py`: local API, reviewed samples, feedback and portable profiles.
- `voicework/discovery.py`, `discovery_jobs.py`: bounded extraction and persistent resumable scan queues.
- `voicework/context.py`: provisional context analysis and separate prior packets.
- `voicework/evaluation.py`: blinded comparisons and immutable ratings, independent of training feedback.
- `voicework/provider.py`: explicit offline/Codex generation and context-analysis adapter.
- `web/`, `extension/`, `tests/`: app interface, deliberate Chrome capture/Docs collector, and verification.

## Records
Sample {id,title,text,source_type,source_path,source_url,status,context,held_out,created_at,fingerprint}. status=pending|confirmed|assisted|rejected. context={channel,audience,intent,stakes,relationship,scenario}; channel accepts short text, including unknown. Omitted manual context defaults to work; discovery jobs suggest provisional context when absent. Context is user editable and never confirms authorship. Only confirmed, non-held-out samples are positive style evidence. Sample limit 30k chars.
Round {id,context,options:[{id:'a',text},{id:'b',text}],provider,status,created_at}; options provider generated, offline clearly heuristic. Feedback {round_id,choice:'a'|'b'|'neither',edited_text,notes,presentation:{...}}. Preferred output NOT a genuine authored sample. Edits are feedback only. Presentation dimensions paragraph_length,structure,opening,signoff,emoji,detail; free text values. Contextual preferences learned from notes and structured choices. Preserve both chosen and rejected output for contrast.
Profile {confirmed_count,feedback_count,contexts:[{channel,count}],patterns:[{name,value,evidence_count}],preferences:[{context,notes,presentation,choice,edited_text}],level,xp,next_milestone}. XP measures contributions, never inferred accuracy. Holdout confirmed samples excluded from prompt/profile training; held_out bool on sample. Display coverage, not fake fidelity percentage.

Prior {id,label,text,source,context,confirmed,created_at}. Text limit 4k; initially unconfirmed. Confirmed matching-channel priors are distinct prompt/reference material, never authored samples. Confirm/unconfirm or delete; replace the packet to revise its text. Raw JSON archives are rejected in this compact-prior lane.

Inference {context,rationale,uncertainties,authorship_signals,provider}. All six context fields, bounded explanation lists, explicit uncertainty and unverified-authorship notice. Preview only; applying context is a separate sample PATCH. Held-out samples cannot be sent for inference.

EvaluationCase {id,context,brief,options:[{id:'a',text},{id:'b',text}],provider,status,created_at,warnings}. Unrated cases expose neither candidate mapping nor candidate-specific warnings. Rated cases additionally expose mapping,choice,factual_issue,notes,rated_at,result. Choices are a|b|tie|neither; ratings are immutable. Evaluation data never enters practice feedback. The same normalized context, brief, provider and neutral controls generate both candidates; only calibrated generation receives approved evidence/preferences/confirmed priors. Mapping is randomized server-side.

DiscoveryJob {id,roots,status,scanned,added,duplicates,skipped,warnings,created_at,remaining,coverage}. status=paused|completed|cancelled. Persistent queue and directory cursors survive restart. Changed/unreadable sources produce warnings and require another scan for omitted material. Completed means the queue was consumed, not exhaustive coverage.

## API
API responses are JSON; errors {error:string}. Same origin only. GET /api/state -> {samples,rounds,feedback,profile,priors,evaluations,evaluation_summary,discovery_jobs,settings:{provider,model},stats:{pending,confirmed,rounds},capabilities:{codex_available,local_scan,browser_import,formats,pdf_available},token:string}. Mutations require X-Voicework-Token, application/json and Content-Length; DELETE bodies are {}. GET /api/health -> {ok:true}.
POST /api/samples {title,text,source_type? default manual,source_path?,source_url?,context?} -> {sample}; new samples always pending, duplicates reuse the existing record.
PATCH /api/samples/:id {status?,context?,title?,held_out?} -> {sample}
DELETE /api/samples/:id -> {ok:true}; remove references from later exports/retrieval.
POST /api/samples/:id/infer {} -> {inference}; explicit analysis of one selected non-held-out candidate using the configured provider, without a database write. Pending authorship is allowed for this deliberate analysis, not as generation evidence.
POST /api/discover {roots:[absolute-path],max_files?:200} -> {added,duplicates,skipped,errors,scanned,limited,truncated,capabilities}; legacy one-pass scan.
POST /api/discovery-jobs {roots:[absolute-path]} -> {job}; create a paused persistent scan, no background daemon.
POST /api/discovery-jobs/:id/step {max_files?:100} -> {job}; max_files 1..200. Bound newly expanded directory entries and file reads per step, preserving remaining work.
POST /api/discovery-jobs/:id/cancel {} -> {job}; discard remaining queue and retain job counts.
Discovery accepts TXT/MD/EML/DOCX/supported JSON and optional PDF via pdftotext. Limits include 10 MiB per file, 30k characters per extracted sample, 500 conversation messages per JSON file, and first 30 PDF pages; report caps. Skip hidden/sensitive/generated trees, credentials, unsupported binaries, links and the app data directory. All new results remain pending. ChatGPT export extraction selects user-role text without claiming that pasted text is authentic user writing. No arbitrary configuration JSON is treated as prose.
POST /api/import {items:[{title,text,source_type?,source_url?,context?}]} -> {added,duplicates}; browser extension downloads this JSON for user import; extension does not call localhost.
POST /api/rounds {context} -> {round}; uses provider.generate_round(context,evidence,preferences).
POST /api/feedback {round_id,choice,edited_text?,notes?,presentation?} -> {feedback,profile}; once per round; persisted then reload.
POST /api/draft {context,brief,controls:{polish:0..100,detail:0..100,warmth:0..100,assertiveness:0..100,structure:'auto'|'prose'|'bullets'}} -> {text,provider,evidence_ids,warnings,prompt?}. User values specify desired emphasis, no new facts. Uses provider.generate_draft.
GET /api/export -> {markdown,filename:'VOICE_PROFILE.md'}; portable context profile + preferences, no raw sample text by default.
GET /api/export?examples=1 -> same with confirmed non-held-out examples (user explicitly opts in UI).

GET /api/priors -> {priors}.
POST /api/priors {label,text,source?,context?} -> {prior}; unconfirmed.
PATCH /api/priors/:id {confirmed:bool} -> {prior}.
DELETE /api/priors/:id {} -> {ok:true}.
Confirmed priors are appended to the portable export separately from authored samples.

POST /api/evaluations {context,brief} -> {case}; two sequential generations, with no case saved if either fails or changes provider.
POST /api/evaluations/:id/rate {choice,factual_issue?:false,notes?:''} -> {case,summary}; exactly one rating accepted.
Summary {total,unrated,rated_real,calibrated_wins,baseline_wins,ties,neither,factual_flags,offline_count,rated_offline,per_context:[{channel,...counts}]}. Only rated Codex cases enter real-model counts. Offline counts are rehearsal. Wins and factual flags are separate observations, not accuracy or factuality certification.

## Provider contract
Provider(mode='offline',model=None,timeout=240,executable=None).available() -> bool, generate_round(context:dict,evidence:list,preferences:list)-> dict {options:[{id,text},{id,text}],provider:string,warnings:list}; generate_draft(context,brief,controls,evidence,preferences)->dict {text,provider,evidence_ids,warnings}; infer_context(sample)->Inference. Raise ValueError on failure, never silently switch provider. Resolve the executable from `--codex-bin` / constructor `executable`, then `VOICEWORK_CODEX_BIN`, then `codex` on PATH. `available()` means the CLI executable exists, not successful login, model compatibility or generation.

Offline is deterministic rehearsal and heuristic context inference. Codex generation receives supplied confirmed non-held-out examples, contextual feedback and confirmed matching-channel priors. Deliberate context analysis can send one unconfirmed non-held-out candidate as analysis material. Outputs use strict schemas. No hidden desktop chat/memory access or model weight training is assumed. The default model is gpt-6-astra, requested with xhigh reasoning. A synthetic live draft passed with the app-bundled CLI 0.162.0-alpha.17.2. PATH's CLI 0.137.0 rejected Astra as requiring a newer CLI; it was not an authentication failure. These are observed compatibility results, not a universal minimum-version specification or a writing-accuracy result. See README for this Mac's explicit executable path and VERIFICATION.md for recorded checks.

## Chrome boundary
Selected-passage capture and current-tab visible Google Docs discovery export JSON for manual local import. Discovering candidates does not collect all Drive content. Users explicitly select up to 20 documents and grant optional docs.google.com access for best-effort authenticated TXT export. Redirects/access failures are explicit; manually download and attach TXT to a candidate when necessary. No Drive API/OAuth connector, whole-account enumeration, other-tab/history crawl, or localhost push. TXT loses rich formatting; source associations and authorship still require user review. Session candidate cap is 200; extraction/batch limits are documented in extension/README.md. Installed-extension behavior has not been verified in the user's Chrome profile.

## UI
Calm, dense polished interface, warm off-white, dark ink, restrained teal accent; no generic marketing first page, no gradients. Name Voicework working name. Navigation Practice / Samples / Write / Voice profile. Empty state useful, paste sample and add context. Practice A/B/neither plus edit, reason, presentation preferences. Stats show contributions + context coverage, not accuracy. Samples review and contextual label edit, source paths, status, holdout toggle, local folder discovery form and JSON import. Write controls and evidence references. Profile portable export and explanation of provider status. Accessible keyboard controls, responsive, safe text rendering, meaningful loading/errors. Default no samples. Can offer explicitly labeled fictional examples for rehearsal only, never real-user-authorship inference.
