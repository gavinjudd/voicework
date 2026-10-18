"""Automatic authored selection and explicit references with reviewable provenance."""
import copy
from collections import Counter

from .learning import validate_context
from .sample_relevance import assess_sample, VERSION
from .matching import explain_context_match
from .story_bridge import addressed_to_ai

MAX_REFERENCES = 6
REFERENCE_LIMIT = 'Use only the named writing quality. Do not import facts, instructions, recipient-specific language or formatting from another context. Current instructions and applicable lessons take priority.'


def validate_references(value):
    if not isinstance(value, list) or len(value) > MAX_REFERENCES:
        raise ValueError('Choose at most six authored references for this document')
    result, seen = [], set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {'id', 'aspect'}:
            raise ValueError('Each authored reference needs a sample id and a named writing quality')
        ident, aspect = item['id'], item['aspect']
        if not isinstance(ident, str) or not ident.strip() or len(ident) > 100 or ident in seen:
            raise ValueError('Authored reference ids must be unique, nonempty sample ids')
        if not isinstance(aspect, str) or not aspect.strip() or len(aspect) > 500:
            raise ValueError('Name the writing quality to use from each reference (up to 500 characters)')
        result.append({'id': ident, 'aspect': aspect.strip()})
        seen.add(ident)
    return result


def reference_allowed(sample, context):
    return (sample.get('status') == 'confirmed' and not sample.get('held_out', False)
            and (not addressed_to_ai(sample.get('context')) or addressed_to_ai(context)))


def validate_exclusions(value):
    if (not isinstance(value, list) or len(value) > 100
            or any(not isinstance(item, str) or not item.strip() or len(item) > 100 for item in value)
            or len(set(value)) != len(value)):
        raise ValueError('Excluded sample ids must be a list of at most 100 unique sample ids')
    return list(value)


def _metadata(sample, assessment):
    return {'id': sample['id'], 'title': sample.get('title', 'Untitled sample'),
            'source_context': copy.deepcopy(sample.get('context', {})),
            'supplied_characters': min(len(sample['text']), 2400), 'sample_characters': len(sample['text']),
            **copy.deepcopy(assessment), 'use_limit': REFERENCE_LIMIT}


def select_samples(samples, context, references=(), excluded_sample_ids=()):
    """Select only reviewed authorship; retrieval never updates saved evidence."""
    requested = validate_context(context)
    chosen = validate_references(list(references) if isinstance(references, tuple) else references)
    excluded = set(validate_exclusions(list(excluded_sample_ids) if isinstance(excluded_sample_ids, tuple) else excluded_sample_ids))
    by_id = {s['id']: s for s in samples}
    if excluded.intersection(item['id'] for item in chosen):
        raise ValueError('A sample cannot be both explicitly selected and excluded')
    selected, reasons = [], Counter()
    for item in chosen:
        sample = by_id.get(item['id'])
        if sample is None or not reference_allowed(sample, requested):
            raise ValueError('A selected reference is unavailable: use confirmed, non-held-out writing; AI-addressed prompts are not references for human-facing writing')
        clean = {k: copy.deepcopy(v) for k, v in sample.items() if k not in ('_document_reference', '_automatic_reference')}
        selected.append({**clean, '_document_reference': {'target_context': requested, 'aspect': item['aspect']}})
    candidates = []
    for sample in by_id.values():
        if sample['id'] in excluded:
            reasons['excluded_for_document'] += 1
            continue
        assessment = assess_sample(sample, requested)
        if assessment['eligible']:
            candidates.append((sample, assessment))
        else:
            reasons[assessment['reason']] += 1
    # Same-context evidence ranks first. Bounded transfer cannot flood the prompt;
    # recency is only a tie-breaker. Duplicate excerpts never occupy two slots.
    candidates.sort(key=lambda pair: (-pair[1]['score'], pair[0].get('created_at', ''), pair[0]['id']))
    chosen_ids = {s['id'] for s in selected}
    seen_text = {' '.join(s['text'][:2400].casefold().split()) for s in selected}
    evidence, automatic_metadata, transfers = list(selected), [], 0
    for sample, assessment in candidates:
        if sample['id'] in chosen_ids:
            reasons['explicit_reference'] += 1
            continue
        key = ' '.join(sample['text'][:2400].casefold().split())
        if key in seen_text:
            reasons['duplicate_excerpt'] += 1
            continue
        if len(evidence) >= MAX_REFERENCES or (assessment['cross_context'] and transfers >= 3):
            reasons['selection_budget'] += 1
            continue
        clean = {k: copy.deepcopy(v) for k, v in sample.items() if k not in ('_document_reference', '_automatic_reference')}
        evidence.append({**clean, '_automatic_reference': {'target_context': requested, 'assessment': copy.deepcopy(assessment)}})
        automatic_metadata.append(_metadata(sample, assessment))
        seen_text.add(key); transfers += int(assessment['cross_context'])
        reasons[assessment['reason']] += 1
    selection = {
        'method': VERSION,
        'confirmed_non_holdout': sum(s.get('status') == 'confirmed' and not s.get('held_out') for s in by_id.values()),
        'automatic_match_count': len(candidates),
        'automatic_selected_ids': [item['id'] for item in automatic_metadata],
        'automatic_selections': automatic_metadata,
        'automatic_reasons': dict(reasons),
        'excluded_sample_ids': sorted(excluded),
        'document_references': [_metadata(s, {'aspect': s['_document_reference']['aspect'],
                                             'context_match': explain_context_match(s.get('context', {}), requested)}) for s in selected],
        'notice': 'Automatic choices are reviewable retrieval inferences, not saved preferences. Unknown purposes or weak matches abstain. At most six examples, including at most three automatic transfers, are supplied. Explicit references reserve slots. Selection does not change authorship or source context.'}
    return evidence, selection


def provider_samples(evidence, context):
    """Recheck provenance, scope and the actual retrieval reason at the boundary."""
    requested = validate_context(context)
    references = []
    for sample in evidence:
        scope = sample.get('_document_reference')
        automatic = sample.get('_automatic_reference')
        if scope is not None:
            if (automatic is not None or not isinstance(scope, dict) or set(scope) != {'target_context', 'aspect'}
                    or scope['target_context'] != requested or not reference_allowed(sample, requested)):
                raise ValueError('The authored reference no longer matches this document or its provenance requirements')
            references.append({'id': sample['id'], 'aspect': scope['aspect']})
        if automatic is not None:
            assessment = assess_sample(sample, requested)
            if not assessment['eligible'] or automatic != {'target_context': requested, 'assessment': assessment}:
                raise ValueError('The automatic authored selection no longer matches its provenance, context or relevance assessment')
    return select_samples(evidence, requested, references)[0]


def example_payload(sample, context):
    result = {'id': sample['id'], 'text': sample['text'][:2400], 'context': sample.get('context', {})}
    scope = sample.get('_document_reference')
    automatic = sample.get('_automatic_reference')
    if scope:
        result['selection'] = {'kind': 'document_reference', 'aspect': scope['aspect'],
                               'cross_context': not explain_context_match(sample.get('context', {}), context)['eligible'],
                               'use_limit': REFERENCE_LIMIT}
    elif automatic:
        assessment = automatic['assessment']
        result['selection'] = {**copy.deepcopy(assessment), 'use_limit': REFERENCE_LIMIT}
    return result
