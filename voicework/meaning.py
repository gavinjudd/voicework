"""Source-relative edit assessments and attributed, consolidated findings."""
import copy
import re
from collections import Counter
from .edit_spans import changed_edits

NEGATION = re.compile(r"\b(?:not|no|never|neither|nor|without|cannot|hardly|unless)\b|\b\w+n[’']t\b", re.I)
COMMITMENT = re.compile(r'\b(?:must|shall|will|may|might|could|should|guarantee|guaranteed)\b', re.I)
PATTERNS = {'negation': NEGATION, 'commitment': COMMITMENT}
# These are per-call budgets, never document-wide coverage cutoffs.
MAX_MEANING_CHANGES = 24
MAX_BATCH_CHANGE_CHARS = 24000
DIMENSIONS = ('none', 'polarity', 'scope', 'actor', 'time', 'condition', 'claim_strength',
              'commitment', 'quantity', 'attribution', 'content', 'uncertain')
LABELS = {'polarity': 'Position reversed', 'scope': 'Scope changed', 'actor': 'Actor changed',
          'time': 'Timing changed', 'condition': 'Condition changed', 'claim_strength': 'Claim strength changed',
          'commitment': 'Commitment changed', 'quantity': 'Quantity changed', 'attribution': 'Attribution changed',
          'content': 'Meaning changed', 'none': 'Meaning appears preserved', 'uncertain': 'Meaning needs review'}
NOTICE = ('Assessments refer to individual edit spans with surrounding context. '
          'Related reconstruction may be assessed together and is labeled. '
          'Preserved is a diagnostic judgment, not proof of fidelity or verified truth. '
          'Missing, uncertain and conflicting assessments remain visible.')


def _counts(text, pattern):
    return Counter(x.casefold() for x in pattern.findall(text))


def plan_meaning_changes(batches):
    plans, omitted, total = [], Counter(), 0
    for units in batches:
        requests = []
        for unit_index, unit in enumerate(units):
            for a, b, ca, cb, scope in changed_edits(unit['source'], unit['draft']):
                context_a, context_b = unit['source'][ca[0]:ca[1]], unit['draft'][cb[0]:cb[1]]
                total += 1
                requests.append({'change_id': 'm%d' % total, 'section': unit['section'], 'unit_index': unit_index,
                    'source_quote': unit['source'][a[0]:a[1]], 'draft_quote': unit['draft'][b[0]:b[1]],
                    'source_context': context_a, 'draft_context': context_b,
                    'source_range': list(a), 'draft_range': list(b), 'assessment_scope': scope})
        plans.append(requests)
    for kind, pattern in PATTERNS.items():
        before = ''.join(unit['source'] for units in batches for unit in units)
        after = ''.join(unit['draft'] for units in batches for unit in units)
        difference = _counts(after, pattern)
        difference.subtract(_counts(before, pattern))
        covered = Counter()
        for request in (item for plan in plans for item in plan):
            covered.update(_counts(request['draft_quote'], pattern))
            covered.subtract(_counts(request['source_quote'], pattern))
        if {k: v for k, v in difference.items() if v} != {k: v for k, v in covered.items() if v}:
            omitted[kind] += 1
    return plans, {key: value for key, value in omitted.items() if value}


def meaning_batches(requests):
    """Schedule every edit in bounded calls, preserving IDs, spans and context."""
    batches, batch, used = [], [], 0
    for request in requests:
        size = len(request['source_context']) + len(request['draft_context'])
        # A candidate is contained in a bounded source/draft review unit. Fail
        # explicitly on an inconsistent plan rather than silently omit it.
        if size > MAX_BATCH_CHANGE_CHARS:
            raise ValueError('An edit context exceeds the review-window budget.')
        if batch and (len(batch) >= MAX_MEANING_CHANGES or used + size > MAX_BATCH_CHANGE_CHARS):
            batches.append(batch)
            batch, used = [], 0
        batch.append(request)
        used += size
    if batch:
        batches.append(batch)
    return batches


def validate_meaning_checks(items):
    if not isinstance(items, list) or len(items) > MAX_MEANING_CHANGES:
        raise ValueError('The model returned invalid meaning assessments.')
    required = {'change_id', 'verdict', 'reason'}
    for item in items:
        if not isinstance(item, dict) or not required <= set(item) or set(item) - (required | {'dimension'}):
            raise ValueError('The model returned an invalid meaning assessment.')
        if item['verdict'] not in ('preserved', 'changed', 'uncertain'):
            raise ValueError('The model returned an invalid meaning verdict.')
        if not isinstance(item['change_id'], str) or not item['change_id'].strip() or len(item['change_id']) > 100:
            raise ValueError('A meaning assessment needs a change identifier.')
        if not isinstance(item['reason'], str) or not item['reason'].strip() or len(item['reason']) > 1000:
            raise ValueError('A meaning assessment needs a bounded explanation.')
        if 'dimension' in item:
            dimension = item['dimension']
            if dimension not in DIMENSIONS or (item['verdict'] == 'preserved' and dimension != 'none') or (item['verdict'] == 'changed' and dimension in ('none', 'uncertain')):
                raise ValueError('The meaning dimension does not match the verdict.')


def meaning_verdicts(result, requests, issue_requests=None):
    requested, reviewed = {item['change_id']: item for item in requests}, {}
    issue_ids = {item['change_id'] for item in (requests if issue_requests is None else issue_requests)}
    for issue in result.get('content_issues', []):
        if any(ident not in issue_ids for ident in issue.get('change_ids', [])):
            raise ValueError('A content finding referenced an unknown edit.')
    for item in result.get('meaning_checks', []):
        ident = item['change_id']
        if ident not in requested or ident in reviewed:
            raise ValueError('The model referenced an unknown or repeated edit.')
        reviewed[ident] = item
    return reviewed


def meaning_report(plans, omitted, verdicts, semantic_complete):
    checks = []
    for request in (item for batch in plans for item in batch):
        ident = request['change_id']
        assessment = verdicts.get(ident, {'verdict': 'uncertain', 'dimension': 'uncertain',
            'reason': 'This edit did not receive an explicit meaning assessment. Review it in context.'})
        check = {key: value for key, value in request.items() if key != 'unit_index'}
        check.update(assessment, reviewed=ident in verdicts)
        check.setdefault('dimension', 'none' if check['verdict'] == 'preserved' else 'content' if check['verdict'] == 'changed' else 'uncertain')
        check['label'] = LABELS[check['dimension']]
        checks.append(check)
    reviewed = sum(item['reviewed'] for item in checks)
    return {'checks': checks, 'total': len(checks), 'reviewed': reviewed, 'unreviewed': len(checks) - reviewed,
            'omitted': sum(omitted.values()), 'omitted_by_kind': omitted,
            'semantic_complete': semantic_complete, 'notice': NOTICE}


def _numeric_attribution(ids, checks, pattern, difference):
    """Attribute residual numeric differences, not balanced clause moves.

    Complete span coverage is checked by the caller first. A token that is
    both added and removed could be a relocation beside a real change to the
    same number. Keep that alarm independent rather than guess which edit
    caused it. Semantic assessments remain independent of this token test.
    """
    residual = {token: count for token, count in difference.items() if count}
    deltas = {}
    for ident in ids:
        delta = _counts(checks[ident]['draft_quote'], pattern)
        delta.subtract(_counts(checks[ident]['source_quote'], pattern))
        deltas[ident] = delta
    for token in residual:
        values = [delta[token] for delta in deltas.values()]
        if any(value > 0 for value in values) and any(value < 0 for value in values):
            return []
    return [ident for ident in ids
            if any(deltas[ident][token] * count > 0 for token, count in residual.items())]


def consolidate_content(deterministic, semantic, report, number_pattern, batches):
    """Group only explicit or locally validated attribution; no fuzzy merge."""
    checks = {item['change_id']: item for item in report['checks']}
    support = {ident: [] for ident in checks}
    signals, independent = [], []

    def record(issue, origin, ids, blocking=True):
        signal = dict(copy.deepcopy(issue), origin=origin, change_ids=list(ids), blocking=blocking)
        signals.append(signal)
        if ids:
            for ident in ids:
                support[ident].append(signal)
        elif blocking:
            independent.append(signal)

    for issue in deterministic:
        code = issue['code']
        pattern = number_pattern if code in ('numbers_changed', 'section_numbers_changed') else NEGATION if code == 'negation_changed' else COMMITMENT if code == 'commitment_changed' else None
        ids = []
        if pattern:
            ids = [ident for ident, item in checks.items() if (not issue.get('section') or issue['section'] == item['section'])
                   and _counts(item['source_quote'], pattern) != _counts(item['draft_quote'], pattern)]
            units = [u for batch in batches for u in batch if not issue.get('section') or u['section'] == issue['section']]
            difference = _counts(''.join(u['draft'] for u in units), pattern)
            difference.subtract(_counts(''.join(u['source'] for u in units), pattern))
            covered = Counter()
            for ident in ids:
                covered.update(_counts(checks[ident]['draft_quote'], pattern))
                covered.subtract(_counts(checks[ident]['source_quote'], pattern))
            lexical = code in ('negation_changed', 'commitment_changed')
            if ((not lexical and report['omitted']) or (lexical and not report['semantic_complete'])
                    or {k: v for k, v in difference.items() if v} != {k: v for k, v in covered.items() if v}):
                ids = []
            elif not lexical:
                ids = _numeric_attribution(ids, checks, pattern, difference)
        lexical = code in ('negation_changed', 'commitment_changed')
        record(issue, 'deterministic', ids, not lexical or not ids)
    for issue in semantic:
        record(issue, 'semantic_review', issue.get('change_ids', []))

    grouped = []
    for ident, item in checks.items():
        entries = support[ident]
        protected = [s for s in entries if s['origin'] == 'deterministic' and s['blocking']]
        model = [s for s in entries if s['origin'] == 'semantic_review']
        changed = item['verdict'] != 'preserved'
        if not changed and not protected and not model:
            continue
        if changed:
            code = 'meaning_changed' if item['verdict'] == 'changed' else 'meaning_uncertain' if item['reviewed'] else 'meaning_not_checked'
            record({'severity': 'warning', 'code': code, 'message': item['reason'], 'section': item['section']}, 'edit_assessment', [ident])
        entries = support[ident]
        conflict = bool(model and item['verdict'] == 'preserved')
        if protected:
            code, message = protected[0]['code'], item['reason'] if changed else protected[0]['message']
            label = item['label'] if item['verdict'] == 'changed' and item['dimension'] == 'quantity' else 'Numeric text changed'
        elif conflict:
            code, label = 'meaning_review_conflict', 'Meaning needs review'
            message = 'The edit assessment found preserved meaning, but the independent review flagged: ' + model[0]['message']
        else:
            code = 'meaning_changed' if item['verdict'] == 'changed' else 'meaning_uncertain' if item['reviewed'] else 'meaning_not_checked'
            message = item['reason']
            label = item['label'] if item['verdict'] == 'changed' else 'Meaning needs review' if item['reviewed'] else 'Meaning not assessed'
        unique = {}
        for signal in entries:
            key = (signal['origin'], signal['code'], signal['message'], signal.get('section', ''))
            unique.setdefault(key, signal)
        evidence = list(unique.values())
        grouped.append({'severity': 'error' if any(s['severity'] == 'error' for s in evidence) else 'warning',
                        'code': code, 'label': label, 'message': message, 'section': item['section'],
                        'change_id': ident, 'dimension': item['dimension'], 'conflicting_assessments': conflict,
                        'evidence': evidence})
    # Unattributed findings may describe distinct changes or interactions.
    # Never merge them merely because they share a section or similar words.
    for signal in independent:
        issue = {key: value for key, value in signal.items() if key not in ('origin', 'blocking', 'change_ids')}
        issue['label'] = 'Wording difference needs review' if issue['code'] in ('negation_changed', 'commitment_changed') else issue['code'].replace('_', ' ').capitalize()
        issue['evidence'] = [signal]
        grouped.append(issue)
    if report['omitted']:
        grouped.append({'severity': 'warning', 'code': 'meaning_review_limited', 'label': 'Some edits need review',
            'section': '', 'message': '%d edit candidates or alignment gaps fell outside the bounded assessment. Review the remaining changes in context.' % report['omitted']})
    return grouped, signals
