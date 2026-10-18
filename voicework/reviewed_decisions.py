"""Explicit acceptance of one reviewed edit; independent diagnostics stay intact."""
import copy
import json

from .document_decisions import _anchor, _text, digest


def edit_key(edit):
    keys = ('section', 'source_quote', 'draft_quote', 'source_range', 'draft_range', 'assessment_scope')
    return digest({**{key: edit[key] for key in keys},
                   'source_context_hash': digest(edit['source_context']),
                   'draft_context_hash': digest(edit['draft_context'])})


def eligible(issue, edit):
    """Only an explicit edit verdict, never uncertainty or protected signals."""
    if (not isinstance(issue, dict) or not isinstance(edit, dict)
            or issue.get('code') != 'meaning_changed' or issue.get('severity') != 'warning'
            or issue.get('conflicting_assessments')
            or not issue.get('change_id') or issue['change_id'] != edit.get('change_id')
            or edit.get('verdict') != 'changed' or edit.get('reviewed') is not True
            or issue.get('dimension') != edit.get('dimension')):
        return False
    from .meaning import DIMENSIONS
    if edit.get('dimension') not in tuple(d for d in DIMENSIONS if d not in ('none', 'uncertain', 'quantity')):
        return False
    evidence = issue.get('evidence')
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= 16:
        return False
    direct = 0
    for signal in evidence:
        if not isinstance(signal, dict) or signal.get('severity') == 'error':
            return False
        if signal.get('origin') == 'edit_assessment' and signal.get('code') == 'meaning_changed':
            if signal.get('change_ids') != [edit['change_id']] or signal.get('message') != edit.get('reason'):
                return False
            direct += 1
        elif not (signal.get('origin') == 'deterministic' and signal.get('blocking') is False
                  and signal.get('code') in ('negation_changed', 'commitment_changed')):
            # A general review can report a different concern in the same span.
            return False
    return direct == 1


def current_edits(source, draft):
    from .documents import _review_units
    from .meaning import plan_meaning_changes
    units = _review_units(source, draft)
    plans, _ = plan_meaning_changes(units)
    return [(edit, batch[edit['unit_index']]) for batch, plan in zip(units, plans) for edit in plan]


def _sentence(text, span):
    from .edit_spans import sentences
    return next((text[a:b] for a, b in sentences(text) if a <= span[0] and span[1] <= b), '')


def preview(source, draft, issue, assessment):
    """Rebuild quote provenance locally; client/model coordinates are untrusted."""
    from .documents import validate_document_text, parse_document
    validate_document_text(source); validate_document_text(draft, output=True)
    if len(json.dumps([issue, assessment], ensure_ascii=False)) > 64000 or not eligible(issue, assessment):
        raise ValueError('Only a reviewed meaning-change warning without independent or protected concerns can be kept here.')
    _text(assessment.get('reason'), 'Review explanation', 1000)
    try:
        requested = edit_key(assessment)
    except (KeyError, TypeError):
        raise ValueError('The edit assessment is incomplete; recheck this draft.') from None
    matches = [(edit, unit) for edit, unit in current_edits(source, draft) if edit_key(edit) == requested]
    if len(matches) != 1:
        raise ValueError('The assessed edit no longer matches this source and draft; recheck them.')
    edit, unit = matches[0]
    a, b = edit['source_quote'].strip(), edit['draft_quote'].strip()
    # Insertions/deletions need a nonempty generation anchor. Use their exact
    # enclosing sentences, rather than infer authorship or invent replacement text.
    if not _anchor(source, a) or not _anchor(draft, b):
        a = _sentence(unit['source'], edit['source_range'])
        b = _sentence(unit['draft'], edit['draft_range'])
    for full, quote in ((source, a), (draft, b)):
        _text(quote, 'Kept wording', 1000)
        if not _anchor(full, quote) or len([x for x in parse_document(full)[0] if x['kind'] == 'prose' and quote in x['text']]) != 1:
            raise ValueError('The kept wording must be unique within one prose paragraph and processing block.')
    if a == b or b in source.replace(a, '', 1):
        raise ValueError('The kept wording is ambiguous outside this source passage.')
    return {'classification': 'reviewed_rephrase', 'code': 'reviewed_meaning_change',
            'source_quote': a, 'draft_quote': b, 'preference_ids': [],
            'message': assessment['reason'], 'basis': 'Explicit article-only acceptance of this reviewed difference, not a claim that meaning is identical.',
            'dimension': assessment['dimension'], 'reviewed_edit_key': requested,
            'reviewed_source_quote': edit['source_quote'], 'reviewed_draft_quote': edit['draft_quote'],
            'assessment_scope': edit['assessment_scope']}


def acknowledge(result, snapshot):
    """Move exact accepted edit verdicts to a visible lane; retain raw evidence."""
    records = [r for r in snapshot.get('items', []) if r['finding'].get('classification') == 'reviewed_rephrase'
               and r.get('applicability') == 'applicable']
    if not records:
        return result
    meaning = result.get('meaning_changes', {})
    complete = (result.get('source_hash') == snapshot['source_hash']
                and result.get('draft_hash') == snapshot.get('draft_hash')
                and result.get('review', {}).get('status') == 'checked'
                and result.get('content', {}).get('semantic') == 'checked'
                and meaning.get('semantic_complete') is True
                and meaning.get('unreviewed') == 0 and meaning.get('omitted') == 0)
    if not complete:
        return result
    edits = {e['change_id']: e for e in meaning['checks']}
    issues = result['content']['issues']; accepted, remaining = [], []
    for issue in issues:
        edit = edits.get(issue.get('change_id')); match = None
        if eligible(issue, edit):
            match = next((r for r in records if r['finding']['reviewed_edit_key'] == edit_key(edit)
                          and r['finding']['dimension'] == edit['dimension']), None)
        if match:
            accepted.append(dict(copy.deepcopy(issue), decision_id=match['id'], decision_reason=match['reason'],
                                 decision_status='accepted_reviewed_difference'))
            edit['accepted_decision_id'] = match['id']
        else:
            remaining.append(issue)
    if not accepted:
        return result
    result['native_status'] = result['status']
    result['content'].update(native_status=result['content']['status'], native_issues=copy.deepcopy(issues),
                             issues=remaining, accepted_issues=accepted)
    result['content']['status'] = 'review_required' if remaining else 'checked'
    # Mirrors the native status inputs. No voice findings, uncertain/missing
    # checks, process warnings or protected errors can be cleared by acceptance.
    result['status'] = ('review_required' if remaining or result['voice'].get('issues') or result.get('warnings') else
                        'checked' if result['voice'].get('status') == 'checked' else 'not_checked')
    return result
