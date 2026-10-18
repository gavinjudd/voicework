"""Bounded, inspectable transfer of writing qualities across sample contexts.

This is a deterministic retrieval heuristic, not a semantic judge or a new
preference. Unknown purposes/boundaries abstain. Labels are never rewritten.
Only the first 2400 characters (the provider's actual excerpt) are inspected.
"""
import re

from .matching import explain_context_match, normalize_label
from .story_bridge import addressed_to_ai

VERSION = 'authored-transfer-v1'
_UNKNOWN = {'', 'unknown', 'unspecified', 'unconfirmed', 'not specified', 'n/a'}
_TECHNICAL = frozenset('''ai data context memory github software code coding system systems
research model models experiment experiments evidence testing tests technical queue batching
workflow workflows tooling tools product design gameplay fleet resources defenses interface
analysis results engineering performance latency algorithm algorithms database databases'''.split())
_STOP = frozenset('''the and for with this that from into about your their our my a an to of in on
is are was were it how why what write writing explain describe process work project personal'''.split())
_PURPOSES = {
    'explanation': r'(?:explain|describe|clarify|unpack) (?:a |an |the )?(?:process|mechanism|workflow|technical research|technical findings|research findings|research|technical investigation|investigation|technical study|study)(?: and (?:its |their |the )?implications)?',
    'problem': r'(?:frame|explain|describe) (?:a |the )?(?:product|technical) problem',
    'proposal': r'(?:propose|evaluate) (?:(?:gameplay|product|technical) )?(?:changes|tradeoffs|alternatives|improvements)(?: with caveats)?',
    'update': r'(?:status update|progress update|share (?:a |the )?progress update)',
    'review': r'(?:request (?:a )?(?:review|feedback)|ask for (?:a )?(?:review|feedback))',
}
_ASPECTS = {
    'explanation': 'Connect an explanation through a cause or a sequence of steps, in ordinary language.',
    'problem': 'Frame a concrete difficulty through a question connected to what the writer wants to achieve.',
    'proposal': 'Connect a concrete observation to a caveat and a tentative alternative.',
    'update': 'Connect what changed to what happens next.',
    'review': 'Make a bounded review request clear and conversational.',
}


def _literal(value):
    return ' '.join(str(value or '').casefold().split())


def _words(value):
    return set(re.findall(r"[a-z][a-z'-]+", _literal(value)))


def purpose(context):
    value = normalize_label('intent', context.get('intent'), context)
    return next((name for name, pattern in _PURPOSES.items() if re.fullmatch(pattern, value)), None)


def _audience(value):
    value = normalize_label('audience', value)
    if re.fullmatch(r'(?:self / )?(?:(?:technical|engineering|product|game-development|development) )?collaborators', value):
        return 'professional peers'
    if value in {'technical readers', 'technical peer', 'technical peers', 'engineers', 'researchers', 'ai researchers',
                 'peer', 'peers', 'colleague', 'colleagues', 'collaborators', 'game collaborators', 'self / game collaborators'}:
        return 'professional peers'
    if value in {'public', 'readers', 'general readers'}:
        return 'public readers'
    return value


def _boundary(source, target):
    # Do not transfer a private/intimate or explicitly formal register into a
    # different setting. These words are boundary guards, not topic signals.
    boundary = re.compile(r'\b(private|confidential|intimate|romantic|legal|academic|formal|casual|informal|marketing|advertising|sales|fiction|poetry)\b')
    for context in (source, target):
        tags = set(boundary.findall(_literal(' '.join(context.get(f, '') for f in ('channel', 'audience', 'relationship', 'intent')))))
        if context is source:
            source_tags = tags
        elif source_tags != tags:
            return 'context_boundary'
    a, b = _audience(source.get('audience')), _audience(target.get('audience'))
    if a in _UNKNOWN or b in _UNKNOWN:
        return 'audience_unknown'
    if a != b and {a, b} != {'professional peers', 'public readers'}:
        return 'audience_conflict'
    a, b = [normalize_label('stakes', ctx.get('stakes')) for ctx in (source, target)]
    if a not in _UNKNOWN and b not in _UNKNOWN and a != b:
        return 'stakes_conflict'
    # A relationship can be absent, but explicit sensitive or asymmetric roles
    # must agree. Peer/product-owner roles can differ for an explanatory quality.
    safe = _UNKNOWN | {'peer', 'peers', 'professional', 'colleague', 'colleagues', 'product owner', 'product owner to collaborators'}
    a, b = [normalize_label('relationship', ctx.get('relationship')) for ctx in (source, target)]
    if a != b and (a not in safe or b not in safe):
        return 'relationship_conflict'
    return None


def _feature(text, kind):
    patterns = {
        'explanation': r'\b(?:because|which means|that(?: is|[’\x27]s) why|essentially|first|then|so that)\b',
        'problem': r'\b(?:how (?:do|can|could|would)|why (?:does|do|is|can)|what would)\b',
        'proposal': r'\b(?:right now|currently|but|however)\b',
        'update': r'\b(?:finished|completed|shipped|changed|now|next)\b',
        'review': r'\b(?:could you|can you|please|would you)\b',
    }
    found = re.search(patterns[kind], text, re.I)
    if not found:
        return None
    if kind == 'proposal' and not re.search(r'\b(?:could|might|consider|perhaps)\b', text, re.I):
        return None
    if kind == 'update' and not re.search(r'\b(?:next|then|remaining|tomorrow)\b', text, re.I):
        return None
    if kind == 'review' and not re.search(r'\b(?:review|check|feedback|look)\b', text, re.I):
        return None
    start = max(0, text.rfind('\n', 0, found.start()) + 1)
    # Show the actual passage inspected, including enough after the marker to
    # let the user decide whether the proposed quality is real.
    start = max(start, found.start() - 100)
    return text[start:start + 400]


def assess_sample(sample, target):
    """No excluded text is read, scored, quoted or returned."""
    result = {'eligible': False, 'reason': '', 'kind': 'automatic_context', 'score': 0,
              'version': VERSION, 'cross_context': False}
    if sample.get('held_out'):
        return dict(result, reason='held_out')
    if sample.get('status') != 'confirmed':
        return dict(result, reason='not_confirmed')
    source = sample.get('context', {})
    if addressed_to_ai(source) and not addressed_to_ai(target):
        return dict(result, reason='ai_addressed')
    match = explain_context_match(source, target)
    source_purpose, target_purpose = purpose(source), purpose(target)
    if match['eligible']:
        if source_purpose and target_purpose and source_purpose != target_purpose:
            return dict(result, reason='purpose_conflict')
        return dict(result, eligible=True, reason=match['reason'], score=100 + match['score'],
                    reasons=['Original channel qualifies under the existing context matcher.',
                             ('Matched fields: ' + ', '.join(match['matched_fields']) + '.') if match['matched_fields']
                             else ('Shared scenario terms: ' + ', '.join(match['scenario_overlap']) + '.') if match['scenario_overlap']
                             else 'Only the channel was specified; relevance within that channel remains broad.'],
                    context_match=match, aspect='Writing in the matched context; current instructions take priority.')
    result.update(kind='automatic_transfer', cross_context=True, context_match=match)
    # Do not turn an unspecified target into an invitation to search all genres.
    if not target_purpose or not source_purpose:
        return dict(result, reason='purpose_unknown')
    allowed = ({'explanation', 'problem', 'proposal'} if target_purpose == 'explanation' else {target_purpose})
    if source_purpose not in allowed:
        return dict(result, reason='purpose_conflict')
    if normalize_label('channel', source.get('channel')) in _UNKNOWN or normalize_label('channel', target.get('channel')) in _UNKNOWN:
        return dict(result, reason='unknown_channel')
    boundary = _boundary(source, target)
    if boundary:
        return dict(result, reason=boundary)
    excerpt = str(sample.get('text', ''))[:2400]
    feature = _feature(excerpt, source_purpose)
    if not feature:
        return dict(result, reason='writing_quality_not_observed')
    target_topic = _words(' '.join(target.get(f, '') for f in ('channel', 'intent', 'scenario'))) - _STOP
    source_topic = _words(excerpt)
    technical = bool(target_topic & {'technical', 'research', 'software', 'engineering', 'gameplay'})
    topic_terms = sorted(source_topic & _TECHNICAL) if technical else sorted(source_topic & target_topic - _STOP)
    # Topic alone never qualifies; a common purpose alone is also insufficient.
    # This deliberately abstains for sparse or novel contexts instead of padding.
    if len(topic_terms) < 2:
        return dict(result, reason='insufficient_topic_evidence')
    return dict(result, eligible=True, reason='transferable_quality', score=10 + min(len(topic_terms), 5),
                purpose=source_purpose, aspect=_ASPECTS[source_purpose], supporting_excerpt=feature,
                topic_terms=topic_terms[:12], reasons=[
                    'Source purpose: %s; requested purpose: %s.' % (source_purpose, target_purpose),
                    'Stored labels indicate compatible audience scope; no detected register, relationship or stakes conflict.',
                    ('Technical/product vocabulary in the supplied excerpt: ' if technical else 'Shared topic words in the supplied excerpt: ') + ', '.join(topic_terms[:12]) + '.',
                    'The quoted passage contains a cue for the proposed writing quality; review whether it fits.'],
                limitations=['Heuristic selection, not an approved preference or a semantic guarantee. Context labels may themselves be provisional.',
                             'Transfer only the named quality. Topic vocabulary does not establish the same subject or facts.'])
