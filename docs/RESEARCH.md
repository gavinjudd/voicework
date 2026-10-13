# Voicework research and reuse decisions

Research checked 2026-10-12 against the primary sources linked below. Product documentation describes vendor capabilities, not independent performance evidence. This is a scoped comparison, not an exhaustive market survey. No benchmark in these sources establishes Voicework's quality.

## Existing products: what is already ordinary

| Product | Primary-source capability | Consequence for Voicework |
| --- | --- | --- |
| Grammarly | A personalized voice profile derived from writing and an on-demand rewrite in that voice; documented for desktop and Chrome. [Official help](https://support.grammarly.com/hc/en-us/articles/23153676821773-View-and-customize-your-voice-profile) | Voice profiles and in-browser rewriting are baseline expectations, not differentiation. |
| Jasper | Brand Voice from text, files, or URLs; editable voice, excerpts, and preview comparisons with and without a voice. [Official help, dated August 28, 2026](https://help.jasper.ai/hc/en-us/articles/55085106001051-Brand-Voice) | Uploading examples, adjustable profiles, and comparison previews are established patterns. |
| Claude styles, historical reference | Anthropic documented custom styles from writing examples and editable instructions. [Official announcement](https://www.anthropic.com/news/styles) and [help article](https://support.anthropic.com/en/articles/10181068-configuring-and-using-styles) were indexed, but direct retrieval failed during this research. | Treat this as a documented predecessor, not a verified claim about today's UI or plan availability. |

A plausible useful distinction is the **combination** of authorship review, separately labeled assisted material, context-specific presentation feedback, preserved rejected alternatives, and a portable evidence-backed profile. This is a product hypothesis, not a claim that competitors lack every component. Gamification should reduce effort and encourage coverage; badges do not make personalization better by themselves.

## What the research supports—and does not

**Retrieve relevant personal evidence before training a model.** LaMP introduces seven personalized tasks and studies profile retrieval including term, semantic, and time-aware matching. It supports testing contextual retrieval as a baseline. Its classification and generation results do not establish authentic voice reproduction or optimal presentation for a particular person. [Salemi et al., ACL 2024](https://aclanthology.org/2024.acl-long.399/)

**Authorship and style are different questions.** Authorship representations can encode topic as well as style; success at an authorship task is not proof of a general style representation. Do not treat a style embedding, a name in the file, or an AI detector as authorship confirmation. Review source history and ask the user. Evaluate across disjoint documents and topics. [Wang et al., TACL 2023](https://aclanthology.org/2023.tacl-1.80/); [Wegmann et al., RepL4NLP 2022](https://aclanthology.org/2022.repl4nlp-1.26/)

**Preserve user agency.** A study of authentic voice in AI co-writing found that positive reactions to personalization coexist with concerns about how it supports the writer. This motivates editable rules and reasons for preferences; it does not prove this interface or gamification will work. [“It was 80% me, 20% AI,” 2024 preprint](https://arxiv.org/abs/2411.13032)

**Preference pairs are a useful data shape, not instant post-training.** DPO is a model-training objective over preferred/rejected responses. The app can preserve compatible evidence now without claiming it updates weights. A choice is only meaningful conditional on its prompt, context, and alternatives; “neither” is not a positive label. [Rafailov et al., 2023](https://arxiv.org/abs/2305.18290)

## Build versus reuse

No packages below are dependencies of this standard-library MVP. Licenses describe the inspected repository, not hosted services, model weights, transitive dependencies, or datasets. Recheck and pin the exact revision before incorporation.

| Candidate | Verified license and primary source | Useful role | Decision |
| --- | --- | --- | --- |
| DSPy | [MIT LICENSE](https://github.com/stanfordnlp/dspy/blob/main/LICENSE); [optimizer documentation](https://dspy.ai/3.2.0/learn/optimization/optimizers/) | MIPROv2 searches instructions and demonstrations; GEPA uses reflective feedback for prompt improvement. | First optimizer experiment after a user-grounded metric and held-out split exist. Do not add it merely to call a model. |
| TextGrad | [MIT LICENSE](https://github.com/zou-group/textgrad/blob/main/LICENSE); [maintainer repository](https://github.com/zou-group/textgrad); [paper](https://arxiv.org/abs/2406.07496) | Propagate textual feedback to improve text variables, including prompts. | Compare against DSPy as an alternative experiment. Critic-generated feedback can optimize for the critic's taste; user outcomes remain the test. |
| Mem0 | [Apache-2.0 LICENSE](https://github.com/mem0ai/mem0/blob/main/LICENSE); [maintainer repository](https://github.com/mem0ai/mem0) | Persistent memory ingestion and search, with library, self-hosted, and hosted options. | Defer until corpus size and retrieval needs justify it. SQLite is sufficient for the first explicit evidence store. Memory extraction must not overwrite authorship, source, or scope. Default model/embedding configuration can involve external services; review configuration first. |
| Hugging Face TRL | [Apache-2.0 LICENSE](https://github.com/huggingface/trl/blob/main/LICENSE); [DPO trainer](https://huggingface.co/docs/trl/main/en/dpo_trainer) | Actual preference training for compatible models using chosen/rejected data. | Later training path, after a prompt baseline, consented data, compute budget, and separate model-license review. Training infrastructure is unnecessary for the first learning loop. |
| LaMP code and benchmark | [Maintainer repository](https://github.com/LaMP-Benchmark/LaMP); [paper](https://aclanthology.org/2024.acl-long.399/) | Evaluation and profile-retrieval research reference. | Borrow experimental ideas; do not vendor code or datasets yet. A license file was not successfully retrieved in this research, so no license or redistribution claim is made. |

Build the product-specific pieces: provenance review, context labels, preference events, correction/deletion behavior, presentation controls, portable profile, and an evaluation interface. Reuse model inference and, later, an optimizer or trainer. A generic agent-memory framework cannot decide whether a passage is the user's own writing or whether a global rule should apply to a sensitive conversation.

## Precise learning terminology

| Mechanism | What changes | Voicework status |
| --- | --- | --- |
| Profile aggregation | Stored descriptive patterns and preference records | MVP |
| In-context calibration | Evidence and instructions supplied to generation | MVP in Codex mode; offline mode is heuristic rehearsal |
| Retrieval improvement | Which approved examples and preferences are selected | Basic scoped selection in the MVP contract; advanced ranking is future work |
| Prompt optimization | Instructions/demonstrations selected using development evaluation | Proposed experiment |
| SFT / DPO | Model weights or adapters trained on examples/preferences | Future work; not implemented or run |

The current [OpenAI DPO guide](https://developers.openai.com/api/docs/guides/direct-preference-optimization), fetched during this research, states that OpenAI is winding down its fine-tuning platform and that it is no longer accessible to new users. Do not make hosted OpenAI fine-tuning a promised product dependency. Keep the data model provider-neutral, verify account eligibility and current availability before any job, and consider an open-weight training experiment only when it earns its operational cost.

The MVP's Codex adapter is inference through a configured CLI. It does not import the desktop assistant's private context, run DPO, or acquire learning by existing in a Codex session. Explicitly available prior knowledge can be supplied in a user-reviewed context packet; it must remain distinct from authentic writing and newly generated feedback.

## Ingestion and browser limits

Chrome's context-menu API exposes optional `selectionText` and page information, which fits explicit passage capture. It does not establish authorship, give complete document access, or preserve full rich-text structure. A browser bridge should show what it captured and fail honestly when there is no usable selection. [Chrome API reference](https://developer.chrome.com/docs/extensions/reference/api/contextMenus)

Google announced the migration of Docs to canvas rendering and warned that extensions depending on document HTML could break, recommending supported Workspace integration points. A generic `window.getSelection()` capture should not be marketed as a reliable Google Docs importer. Use copy/paste or a selected document export as the MVP fallback. [Google Workspace announcement, May 2021](https://workspaceupdates.googleblog.com/2021/05/Google-Docs-Canvas-Based-Rendering-Update.html)

Google Drive documents can be exported to plain text, DOCX, and Markdown among other formats. A later connector can use selected-file access and a supported export path; investigate the exact API and format needed before implementation. Google's scope guidance recommends narrow access and describes `drive.file` with a picker. The MVP has no Drive OAuth connector, account-wide sync, or source refresh. [Export formats](https://developers.google.com/workspace/drive/api/guides/ref-export-formats); [scope guidance](https://developers.google.com/workspace/drive/api/guides/api-specific-auth)

For local imports, record file path, fingerprint, extraction method, and source date where available. Preserve paragraph boundaries. Rich text headings, list styles, and signatures can be lost during extraction, so missing formatting is unknown evidence, not a preference against formatting. Files on a different PC require a running local instance, mounted path, or explicit transfer; Chrome being open does not make that filesystem available.

## Evaluation plan and failure modes

Use these as engineering proposals, not claimed results:

1. **Freeze evidence.** Group original documents, revisions, quoted copies, and thread excerpts before splitting. Reserve test groups before profile construction. Exact-hash deduplication alone is insufficient.
2. **Fix the task.** For each test scenario, preserve the same factual brief, audience, channel, intent, and stakes. Compare generic generation, a static profile, and contextual profile plus feedback using the same provider/model settings.
3. **Keep evaluation separate.** Optimize on training/development examples. Use validation for selecting a version and a final test set for reporting. Never turn test ratings into training feedback while continuing to call that set held out.
4. **Measure practical benefit.** Randomize paired display order; collect preference, ties/neither, edit time or revision effort, presentation fit, and factual errors. Report counts and per-context coverage with uncertainty. Do not derive a universal voice percentage from a handful of votes.
5. **Test control extremes.** Low/high polish, detail, warmth, assertiveness, prose/bullets must preserve entities, numbers, commitments, attribution, and certainty. A preferred but factually wrong draft fails the factual criterion.
6. **Test generalization separately.** In-context tests use new source material in familiar situations; transfer tests use unfamiliar contexts. Report them separately rather than pooling an easy work-email context with an unseen personal situation.

Main risks are self-reinforcing AI samples, topic leakage mistaken for style, globally applying a local preference, flattering language mistaken for improvement, and optimizing a model judge instead of the person. Counter them with typed provenance, source-group splits, editable scoped rules, factual constraints, and user judgments. An LLM judge may help surface issues, but it cannot certify authorship or the user's preference.

## Next decision

Run the manual review–practice–draft loop in one real context before adopting optimization or training infrastructure. If the user still rewrites substantially, inspect whether the cause is wrong facts, missing context, bad presentation, or voice mismatch. Each implies a different fix; a bigger model or more samples is not automatically the answer.
